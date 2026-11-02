import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QIODevice, QSettings
    from PySide6.QtGui import QImage
    from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from opldeck.covers.artwork import MAX_IMAGE_BYTES, decode_image, encode_image
    from opldeck.covers.cover_network import CoverFetcher
    from opldeck.ui.game_gui import GameDialog
    from opldeck.ui.gui import iso_rows
    from opldeck.covers.providers import PROVIDERS, ProviderPreferences
    from opldeck.ui.settings_gui import SettingsDialog
    from opldeck.ui.themes import ThemeManager, palette_snapshot
except ModuleNotFoundError:
    QApplication = None

from tests.fixtures import make_iso


if QApplication is not None:
    class Reply(QNetworkReply):
        """Qt reply with real headers/signals and a controlled in-memory transport."""

        def __init__(self):
            super().__init__()
            self.buffer = b""
            self.aborted = False
            self.open(QIODevice.OpenModeFlag.ReadOnly)

        def headers(self, status=200, length=None):
            if status is not None:
                self.setAttribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute, status)
            if length is not None:
                self.setRawHeader(b"Content-Length", str(length).encode())
            self.metaDataChanged.emit()

        def feed(self, data):
            self.buffer += data
            self.readyRead.emit()

        def readData(self, maximum):
            data, self.buffer = self.buffer[:maximum], self.buffer[maximum:]
            return data

        def bytesAvailable(self):
            return len(self.buffer) + super().bytesAvailable()

        def isSequential(self):
            return True

        def finish(self, error=QNetworkReply.NetworkError.NoError):
            if error != QNetworkReply.NetworkError.NoError:
                self.setError(error, "simulated connection error")
            self.setFinished(True)
            self.finished.emit()

        def abort(self):
            self.aborted = True
            self.finish(QNetworkReply.NetworkError.OperationCanceledError)


    class Manager:
        def __init__(self):
            self.requests = []

        def get(self, request):
            self.requests.append(request)
            self.reply = Reply()
            return self.reply


@unittest.skipIf(QApplication is None, "PySide6 não instalado")
class CoverNetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = QSettings(str(self.root / "prefs.ini"), QSettings.Format.IniFormat)
        self.manager = Manager()
        self.fetcher = CoverFetcher(manager=self.manager)
        self.addCleanup(self.fetcher.cancel)
        self.results = []
        self.fetcher.finished.connect(lambda result, error: self.results.append((result, error)))
        self.image = QImage(80, 120, QImage.Format.Format_RGB32)
        self.image.fill(0xff125678)
        self.data = encode_image(self.image, ".jpg")

    def start(self):
        self.fetcher.start("xlenore", "SLUS_207.18")
        return self.manager.reply

    def success(self, reply):
        reply.headers(length=len(self.data))
        reply.feed(self.data[:100])
        reply.feed(self.data[100:])
        reply.finish()

    def dialog(self, enabled=True, **kwargs):
        self.settings.setValue("providers/enabled", enabled)
        self.settings.sync()
        dvd = self.root / "DVD"
        dvd.mkdir(exist_ok=True)
        path = make_iso(dvd / "Synthetic.iso")
        dialog = GameDialog(iso_rows([str(path)])[0], "ISO", "Teste", settings=self.settings,
                            fetcher=self.fetcher, **kwargs)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_success_url_headers_progress_and_validated_image(self):
        progress = []
        self.fetcher.progress.connect(lambda current, total: progress.append((current, total)))
        reply = self.start()
        request = self.manager.requests[0]
        self.assertEqual(request.url().toString(),
                         "https://raw.githubusercontent.com/xlenore/ps2-covers/main/covers/default/SLUS-20718.jpg")
        self.assertEqual(bytes(request.rawHeader("Accept-Encoding")), b"identity")
        self.success(reply)
        result, error = self.results[0]
        self.assertFalse(error)
        self.assertEqual(result.image.size(), self.image.size())
        self.assertEqual(result.byte_count, len(self.data))
        self.assertIn((len(self.data), len(self.data)), progress)
        self.assertFalse(self.fetcher.busy)
        self.assertFalse(self.fetcher.timer.isActive())

    def test_invalid_provider_or_id_never_requests_network(self):
        for provider, game_id in (("unknown", "SLUS_207.18"), ("xlenore", "../../other")):
            with self.assertRaises(ValueError):
                self.fetcher.start(provider, game_id)
        self.assertFalse(self.manager.requests)

    def test_http_failures_and_redirects_do_not_fetch_other_urls(self):
        for status, message in ((404, "não encontrada"), (429, "limitou"), (403, "recusou"),
                                (302, "redirecionado"), (500, "HTTP 500")):
            with self.subTest(status=status):
                reply = self.start()
                reply.headers(status)
                self.assertIn(message, self.results[-1][1])
                self.assertTrue(reply.aborted)
        self.assertEqual(len(self.manager.requests), 5)

    def test_invalid_and_oversized_content_length_abort_before_body(self):
        for length in (-1, "garbage", MAX_IMAGE_BYTES + 1):
            reply = self.start()
            reply.headers(length=length)
            self.assertIsNone(self.results[-1][0])
            self.assertTrue(reply.aborted)
            self.assertFalse(self.fetcher.data)

    def test_stream_limit_without_content_length(self):
        reply = self.start()
        reply.headers()
        reply.feed(b"x" * (MAX_IMAGE_BYTES + 1))
        self.assertIn("20 MiB", self.results[-1][1])
        self.assertTrue(reply.aborted)

    def test_truncated_and_non_image_responses_are_not_previews(self):
        reply = self.start()
        reply.headers(length=len(self.data) + 1)
        reply.feed(self.data)
        reply.finish()
        self.assertIn("incompleta", self.results[-1][1])
        reply = self.start()
        reply.headers()
        reply.feed(b"<html>upstream error</html>")
        reply.finish()
        self.assertIn("Imagem inválida", self.results[-1][1])

    def test_image_without_length_can_complete(self):
        reply = self.start()
        reply.headers()
        reply.feed(self.data)
        reply.finish()
        self.assertIsNotNone(self.results[-1][0])

    def test_connection_failure_timeout_and_missing_http_status(self):
        reply = self.start()
        reply.finish(QNetworkReply.NetworkError.HostNotFoundError)
        self.assertIn("conexão", self.results[-1][1])
        reply = self.start()
        self.fetcher.timer.timeout.emit()
        self.assertIn("Tempo", self.results[-1][1])
        self.assertTrue(reply.aborted)
        reply = self.start()
        reply.finish()
        self.assertIn("não confirmou", self.results[-1][1])

    def test_cancel_then_retry_ignores_late_signals(self):
        old = self.start()
        with self.assertRaises(ValueError):
            self.start()
        self.fetcher.cancel()
        self.assertEqual(len(self.results), 1)
        current = self.start()
        old.feed(b"late data")
        old.finished.emit()
        self.assertEqual(len(self.results), 1)
        self.success(current)
        self.assertEqual(len(self.results), 2)
        self.assertIsNotNone(self.results[-1][0])

    def test_disabled_or_read_only_dialog_does_not_start(self):
        for kwargs in ({"enabled": False}, {"editable": False}):
            dialog = self.dialog(**kwargs)
            self.assertFalse(dialog.search_button.isEnabled())
            dialog.search_cover()
        self.assertFalse(self.manager.requests)

    def test_download_only_previews_then_save_preserves_game(self):
        dialog = self.dialog()
        game = self.root / "DVD" / "Synthetic.iso"
        before = game.read_bytes()
        dialog.search_cover()
        self.assertFalse(dialog.choose_button.isEnabled())
        self.success(self.manager.reply)
        self.assertFalse((self.root / "ART").exists())
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.save()
        path = self.root / "ART" / "SLUS_123.45_COV.png"
        self.assertEqual(decode_image(path.read_bytes()).size(), self.image.size())
        self.assertEqual(game.read_bytes(), before)

    def test_download_preview_does_not_reload_library_or_accept_external_change(self):
        art = self.root / 'ART'
        art.mkdir()
        path = art / 'SLUS_123.45_COV.jpg'
        path.write_bytes(self.data)
        dialog = self.dialog()
        state = dialog.state
        dialog.search_cover()
        path.write_bytes(b'external change during download')
        with patch('pathlib.Path.iterdir', side_effect=AssertionError('Unexpected library scan')), \
                patch('opldeck.covers.artwork._regular_reader', side_effect=AssertionError('Unexpected cover read')):
            self.success(self.manager.reply)
        self.assertIs(dialog.state, state)
        self.assertIs(dialog.current_image, dialog.pending_image)
        self.assertEqual(dialog.current_image.size(), self.image.size())
        self.assertTrue(dialog.save_button.isEnabled())
        with patch('opldeck.ui.game_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes), \
                patch('opldeck.ui.game_gui.QMessageBox.warning') as warning:
            dialog.save()
        self.assertIn('mudou', warning.call_args.args[2])
        self.assertEqual(path.read_bytes(), b'external change during download')

    def test_replacement_declined_preserves_existing_cover(self):
        dialog = self.dialog()
        dialog.pending_image = self.image
        dialog.reload_cover()
        dialog.save()
        path = dialog.saved_paths[0]
        before = path.read_bytes()
        dialog.search_cover()
        self.success(self.manager.reply)
        with patch("opldeck.ui.game_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            dialog.save()
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNotNone(dialog.pending_image)

    def test_failed_search_preserves_unsaved_preview_and_allows_retry(self):
        dialog = self.dialog()
        dialog.pending_image = self.image
        dialog.reload_cover()
        with patch("opldeck.ui.game_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog.search_cover()
        self.manager.reply.headers(404)
        self.assertIs(dialog.pending_image, self.image)
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertTrue(dialog.search_button.isEnabled())
        self.assertFalse((self.root / "ART").exists())

    def test_close_aborts_request_without_creating_art(self):
        dialog = self.dialog()
        dialog.show()
        dialog.search_cover()
        dialog.close()
        self.assertTrue(self.manager.reply.aborted)
        self.assertFalse(dialog.isVisible())
        self.assertFalse((self.root / "ART").exists())

    def test_provider_defaults_and_unknown_settings(self):
        self.assertFalse(ProviderPreferences.load(self.settings).enabled)
        self.settings.setValue("providers/enabled", True)
        self.settings.setValue("providers/cover", "https://untrusted.example")
        self.assertFalse(ProviderPreferences.load(self.settings).enabled)
        self.assertEqual(ProviderPreferences.load(self.settings).provider_id, "xlenore")

    def theme_manager(self):
        style, palette = self.app.style().objectName(), palette_snapshot(self.app.palette())
        manager = ThemeManager(self.app, self.settings)
        def cleanup():
            self.app.removeEventFilter(manager)
            manager.deleteLater()
            self.app.setStyle(style)
            self.app.setPalette(palette)
        self.addCleanup(cleanup)
        return manager

    def test_settings_cancel_then_apply_persist_across_dialogs(self):
        manager = self.theme_manager()
        dialog = SettingsDialog(manager=manager, initial_tab="providers")
        self.addCleanup(dialog.deleteLater)
        dialog.provider_enabled.setChecked(True)
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.reject()
        self.assertFalse(ProviderPreferences.load(self.settings).enabled)
        dialog = SettingsDialog(manager=manager, initial_tab="providers")
        self.addCleanup(dialog.deleteLater)
        dialog.provider_enabled.setChecked(True)
        dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("dark"))
        self.assertTrue(dialog.save())
        dialog.provider_enabled.setChecked(False)
        dialog.reject()
        restored = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        self.assertTrue(ProviderPreferences.load(restored).enabled)
        self.assertEqual(restored.value("appearance/theme"), "dark")

    def test_failed_settings_save_keeps_provider_disabled(self):
        blocked = self.root / "blocked"
        blocked.write_text("file")
        self.settings = QSettings(str(blocked / "prefs.ini"), QSettings.Format.IniFormat)
        dialog = SettingsDialog(manager=self.theme_manager(), initial_tab="providers")
        self.addCleanup(dialog.deleteLater)
        dialog.provider_enabled.setChecked(True)
        self.assertFalse(dialog.save())
        self.assertFalse(ProviderPreferences.load(self.settings).enabled)
        self.assertIn("Não foi possível salvar", dialog.feedback.text())
