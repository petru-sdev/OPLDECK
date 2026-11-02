import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from threading import Event
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtGui import QImage
    from PySide6.QtNetwork import QNetworkReply
    from PySide6.QtWidgets import QApplication, QMessageBox
    from opldeck.covers.artwork import encode_image, load_cover
    from opldeck.covers.cover_network import CoverFetcher
    from opldeck.covers.cover_preload import CoverKey, CoverPreloader
    from opldeck.library.devices import Volume
    from opldeck.ui.game_gui import GameDialog
    from opldeck.ui.gui import MainWindow, iso_rows
    from tests.covers.test_cover_network import Manager
except ModuleNotFoundError:
    QApplication = None

from tests.fixtures import make_iso


@unittest.skipIf(QApplication is None, 'PySide6 não instalado')
class CoverPreloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.library = self.root / 'usb'
        (self.library / 'DVD').mkdir(parents=True)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.settings.setValue('providers/enabled', True)
        self.settings.sync()
        self.manager = Manager()
        self.fetcher = CoverFetcher(manager=self.manager)
        self.service = CoverPreloader(settings=self.settings, fetcher=self.fetcher)
        self.addCleanup(self.cleanup)
        self.image = QImage(80, 120, QImage.Format.Format_RGB32)
        self.image.fill(0xff123456)
        self.data = encode_image(self.image, '.png')
        self.path = make_iso(self.library / 'DVD' / 'Synthetic.iso')
        self.values = iso_rows([str(self.path)])[0]
        self.key = CoverKey.make(self.library, self.values[1], self.values[2], self.path.stem)

    def cleanup(self):
        self.service.close()
        self.service.pool.waitForDone(5000)
        self.app.processEvents()

    def wait(self, predicate, message='Pré-carregamento não concluiu'):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.002)
        self.fail(message)

    def finish(self, reply=None):
        reply = reply or self.manager.reply
        reply.headers(length=len(self.data))
        reply.feed(self.data)
        reply.finish()

    def start_network(self, keys=None):
        self.service.preload(keys or [self.key])
        self.wait(lambda: self.fetcher.busy)
        return self.manager.reply

    def warm(self):
        self.start_network()
        self.finish()
        self.wait(lambda: not self.service.busy)

    def local_cover(self, key=None):
        key = key or self.key
        art = key.root / 'ART'
        art.mkdir(exist_ok=True)
        path = art / f'{key.game_id}_COV.png'
        path.write_bytes(self.data)
        return path

    def dialog(self, **kwargs):
        dialog = GameDialog(self.values, 'ISO', '', settings=self.settings, preloader=self.service, **kwargs)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_preload_downloads_without_writing_then_ficha_saves_explicitly(self):
        before = self.path.read_bytes()
        self.warm()
        self.assertFalse((self.library / 'ART').exists())
        with patch('opldeck.covers.cover_preload.load_cover', side_effect=AssertionError('Cache should be ready')):
            dialog = self.dialog()
            self.assertTrue(dialog.save_button.isEnabled())
            self.assertIsNotNone(dialog.pending_image)
        self.assertEqual(len(self.manager.requests), 1)
        dialog.save()
        self.wait(lambda: not dialog.cover_loading)
        self.assertTrue((self.library / 'ART' / 'SLUS_123.45_COV.png').is_file())
        self.assertIsNone(dialog.pending_image)
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertEqual(self.path.read_bytes(), before)

    def test_existing_local_cover_is_warmed_without_network_or_pending_save(self):
        path = self.local_cover()
        self.service.preload([self.key])
        self.wait(lambda: not self.service.busy)
        self.assertFalse(self.manager.requests)
        dialog = self.dialog()
        self.assertEqual(dialog.current_cover_path, path)
        self.assertIsNotNone(dialog.current_image)
        self.assertFalse(dialog.save_button.isEnabled())

    def test_large_legacy_is_prepared_in_background_without_writing(self):
        image = QImage(1024, 2048, QImage.Format.Format_RGB32)
        image.fill(0xff123456)
        path = self.local_cover().with_suffix('.jpg')
        path.with_suffix('.png').unlink()
        original = encode_image(image, '.jpg')
        path.write_bytes(original)
        self.service.preload([self.key])
        self.wait(lambda: not self.service.busy)
        self.assertFalse(self.manager.requests)
        prepared = self.service.peek(self.key)
        self.assertTrue(prepared.local.needs_save)
        self.assertEqual(prepared.local.image.height(), 512)
        self.assertLessEqual(prepared.size, 512 * 512 * 4)
        self.assertEqual(list(path.parent.iterdir()), [path])
        with patch('opldeck.covers.cover_preload.load_cover', side_effect=AssertionError('Use prepared cache')):
            dialog = self.dialog()
            self.assertTrue(dialog.save_button.isEnabled())
            self.assertEqual(dialog.pending_image.height(), 512)
        dialog.save()
        self.wait(lambda: not dialog.cover_loading)
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(path.with_suffix('.png').is_file())
        self.assertFalse(dialog.save_button.isEnabled())

    def test_download_is_scaled_before_entering_cache(self):
        image = QImage(2048, 1024, QImage.Format.Format_RGB32)
        image.fill(0xff123456)
        self.data = encode_image(image, '.png')
        self.warm()
        prepared = self.service.peek(self.key)
        self.assertEqual(prepared.download.image.width(), 512)
        self.assertEqual(prepared.download.image.height(), 256)
        self.assertLessEqual(prepared.size, 512 * 512 * 4)
        self.assertFalse((self.library / 'ART').exists())

    def test_disabled_provider_still_loads_local_but_never_uses_network(self):
        self.settings.setValue('providers/enabled', False)
        self.service.preload([self.key])
        self.wait(lambda: not self.service.busy)
        self.assertFalse(self.manager.requests)
        self.assertIsNone(self.service.peek(self.key).download)

    def test_duplicate_requests_and_serials_share_download(self):
        other = CoverKey.make(self.library, self.key.game_id, 'Other title', 'Other')
        self.start_network([self.key, self.key, other])
        self.wait(lambda: self.service.worker is None and not self.service.local_queue)
        self.finish()
        self.wait(lambda: not self.service.busy)
        self.assertEqual(len(self.manager.requests), 1)
        self.assertIsNotNone(self.service.peek(other).download)
        self.service.request(self.key)
        self.app.processEvents()
        self.assertEqual(len(self.manager.requests), 1)

    def test_ficha_and_event_loop_remain_responsive_during_slow_local_read(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(3)
            return load_cover(*args, **kwargs)
        with patch('opldeck.covers.cover_preload.load_cover', side_effect=delayed):
            dialog = self.dialog()
            dialog.show()
            self.wait(entered.is_set)
            ticks = []
            QTimer.singleShot(0, lambda: ticks.append(True))
            self.wait(lambda: bool(ticks))
            self.assertTrue(dialog.isVisible())
            self.assertTrue(dialog.cover_loading)
            self.assertFalse(dialog.save_button.isEnabled())
            release.set()
            self.wait(lambda: self.fetcher.busy)
        self.finish()
        self.assertTrue(dialog.save_button.isEnabled())

    def test_late_automatic_result_does_not_replace_manually_chosen_preview(self):
        self.start_network()
        dialog = self.dialog()
        chosen = self.root / 'chosen.png'
        image = self.image.copy()
        image.fill(0xfffedcba)
        chosen.write_bytes(encode_image(image, '.png'))
        with patch('opldeck.ui.game_gui.QFileDialog.getOpenFileName', return_value=(str(chosen), '')):
            dialog.choose_cover()
        self.finish()
        self.assertEqual(dialog.pending_image, image)
        self.assertEqual(dialog.pending_source, 'Arquivo local')
        self.assertFalse(dialog.preloaded_pending)

    def test_automatic_preview_closes_quietly_and_can_be_reopened_from_cache(self):
        self.warm()
        dialog = self.dialog()
        dialog.show()
        with patch('opldeck.ui.game_gui.QMessageBox.question', side_effect=AssertionError('Unexpected prompt')):
            dialog.reject()
        self.assertFalse(dialog.isVisible())
        self.assertFalse((self.library / 'ART').exists())
        self.assertIsNotNone(self.dialog().pending_image)

    def test_cached_guard_refuses_file_created_externally_before_save(self):
        self.warm()
        path = self.local_cover()
        before = path.read_bytes()
        dialog = self.dialog()
        with patch('opldeck.ui.game_gui.QMessageBox.warning') as warning:
            dialog.save()
        self.assertIn('mudou', warning.call_args.args[2])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((path.parent / 'SLUS_123.45_COV.jpg').exists())

    def test_discard_does_not_reapply_background_preview(self):
        self.warm()
        dialog = self.dialog()
        dialog.discard()
        self.wait(lambda: self.fetcher.busy)
        self.finish()
        self.assertIsNone(dialog.pending_image)
        self.assertFalse(dialog.save_button.isEnabled())

    def test_missing_cover_continues_queue_but_network_failure_stops_it(self):
        other = CoverKey.make(self.library, 'SLUS_111.11')
        self.start_network([self.key, other])
        self.manager.reply.headers(404)
        self.wait(lambda: len(self.manager.requests) == 2)
        self.manager.reply.finish(QNetworkReply.NetworkError.HostNotFoundError)
        self.wait(lambda: not self.service.busy)
        self.assertTrue(self.service.network_paused)
        self.service.preload([CoverKey.make(self.library, 'SLUS_222.22')])
        self.wait(lambda: not self.service.busy)
        self.assertEqual(len(self.manager.requests), 2)

    def test_rate_limit_stops_remaining_requests(self):
        other = CoverKey.make(self.library, 'SLUS_111.11')
        self.start_network([self.key, other])
        self.manager.reply.headers(429)
        self.wait(lambda: not self.service.busy)
        self.assertEqual(len(self.manager.requests), 1)
        self.assertFalse(self.service.peek(self.key).pending)

    def test_long_queue_keeps_pending_entries_bounded_and_completes_all_downloads(self):
        self.service.max_entries = 3
        keys = [CoverKey.make(self.library, f'SLUS_111.{i:02}') for i in range(12)]
        self.service.preload(keys)
        completed = []
        self.service.ready.connect(lambda key, entry: completed.append(key) if entry.download else None)
        for index in range(len(keys)):
            self.wait(lambda: self.fetcher.busy)
            self.assertLessEqual(len(self.service.cache), 3)
            self.finish()
        self.wait(lambda: not self.service.busy)
        self.assertEqual(set(completed), set(keys))
        self.assertEqual(len(self.manager.requests), len(keys))
        self.assertFalse((self.library / 'ART').exists())

    def test_provider_change_preserves_guard_for_pending_preview(self):
        self.warm()
        dialog = self.dialog()
        state = dialog.state
        path = self.local_cover()
        self.settings.setValue('providers/enabled', False)
        self.service.sync_preferences()
        self.assertIs(dialog.state, state)
        with patch('opldeck.ui.game_gui.QMessageBox.warning') as warning:
            dialog.save()
        self.assertIn('mudou', warning.call_args.args[2])
        self.assertEqual(path.read_bytes(), self.data)

    def test_readonly_device_can_preload_but_save_guard_blocks_writing(self):
        from opldeck.library.devices import require_volume
        volume = Volume(self.library, '/dev/test1', 'Test', '100', readonly=True)
        with patch('opldeck.library.devices.mounts', return_value={str(self.library): ('100', '/dev/test1', True)}):
            self.service.reset(volume)
            self.warm()
            dialog = self.dialog(library_guard=lambda root, write: require_volume(volume, write=write))
            with patch('opldeck.ui.game_gui.QMessageBox.warning') as warning:
                dialog.save()
            self.assertIn('gravação', warning.call_args.args[2])
            self.assertFalse((self.library / 'ART').exists())

    def test_close_during_slow_local_io_does_not_block_interface(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(3)
            return load_cover(*args, **kwargs)
        with patch('opldeck.covers.cover_preload.load_cover', side_effect=delayed):
            self.service.preload([self.key])
            self.wait(entered.is_set)
            self.service.close()
            ticks = []
            QTimer.singleShot(0, lambda: ticks.append(True))
            self.wait(lambda: bool(ticks))
            self.assertFalse(release.is_set())
            release.set()
            self.wait(lambda: not self.service.busy)
        self.assertFalse(self.service.cache)

    def test_reset_cancels_network_and_ignores_late_reply(self):
        old = self.start_network()
        dialog = self.dialog()
        self.service.reset()
        self.assertTrue(old.aborted)
        old.feed(self.data)
        old.finished.emit()
        self.assertFalse(self.service.cache)
        self.assertIsNone(dialog.state)
        self.assertFalse(dialog.save_button.isEnabled())

    def test_reset_during_local_read_does_not_restore_old_cache(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(3)
            return load_cover(*args, **kwargs)
        with patch('opldeck.covers.cover_preload.load_cover', side_effect=delayed):
            self.service.preload([self.key])
            self.wait(entered.is_set)
            self.service.reset()
            release.set()
            self.wait(lambda: not self.service.busy)
        self.assertFalse(self.service.cache)
        self.assertFalse(self.manager.requests)

    def test_force_refresh_ignores_previous_read_of_same_game(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        def delayed(*args, **kwargs):
            loaded = load_cover(*args, **kwargs)
            entered.set()
            release.wait(3)
            return loaded
        with patch('opldeck.covers.cover_preload.load_cover', side_effect=delayed):
            self.service.preload([self.key])
            self.wait(entered.is_set)
            self.local_cover()
            self.service.request(self.key, force=True)
            release.set()
            self.wait(lambda: not self.service.busy)
        self.assertIsNotNone(self.service.peek(self.key).local.image)
        self.assertFalse(self.manager.requests)

    def test_disable_provider_cancels_pending_requests(self):
        old = self.start_network()
        self.settings.setValue('providers/enabled', False)
        self.settings.sync()
        self.service.sync_preferences()
        self.assertTrue(old.aborted)
        self.service.preload([self.key])
        self.wait(lambda: not self.service.busy)
        self.assertEqual(len(self.manager.requests), 1)

    def test_transfer_pause_cancels_download_and_resumes_without_stale_result(self):
        old = self.start_network()
        self.service.set_paused(True)
        self.assertTrue(old.aborted)
        self.app.processEvents()
        self.assertEqual(len(self.manager.requests), 1)
        self.service.set_paused(False)
        self.wait(lambda: len(self.manager.requests) == 2)
        old.finished.emit()
        self.assertTrue(self.fetcher.busy)
        self.finish()
        self.assertIsNotNone(self.service.peek(self.key).download)

    def test_cache_limits_memory_and_evicts_oldest_then_reloads_on_demand(self):
        self.service.max_bytes = self.image.sizeInBytes() * 2
        keys = [CoverKey.make(self.library, f'SLUS_111.{i:02}') for i in range(4)]
        for key in keys:
            self.local_cover(key)
        self.service.preload(keys)
        self.wait(lambda: not self.service.busy)
        self.assertLessEqual(self.service.cache_bytes, self.service.max_bytes)
        self.assertLessEqual(len(self.service.cache), 2)
        self.assertIsNone(self.service.peek(keys[0]))
        self.service.request(keys[0])
        self.wait(lambda: not self.service.busy)
        self.assertIsNotNone(self.service.peek(keys[0]).local.image)

    def test_cache_entry_limit_bounds_missing_covers(self):
        self.settings.setValue('providers/enabled', False)
        self.service.max_entries = 2
        self.service.preload([CoverKey.make(self.library, f'SLUS_111.{i:02}') for i in range(8)])
        self.wait(lambda: not self.service.busy)
        self.assertEqual(len(self.service.cache), 2)

    def test_open_game_is_prioritized_in_queue(self):
        keys = [CoverKey.make(self.library, f'SLUS_111.{i:02}') for i in range(3)]
        self.service.set_paused(True)
        self.service.preload(keys)
        self.service.request(keys[-1])
        self.service.set_paused(False)
        self.wait(lambda: self.fetcher.busy)
        self.assertIn('SLUS-11102', self.manager.requests[0].url().toString())

    def test_close_drops_queue_and_cancels_download(self):
        old = self.start_network()
        self.service.close()
        self.assertTrue(old.aborted)
        self.assertFalse(self.service.cache)
        self.service.request(self.key)
        self.app.processEvents()
        self.assertEqual(len(self.manager.requests), 1)

    def test_detected_device_automatically_preloads_and_removal_clears_cache(self):
        volume = Volume(self.library, '/dev/test1', 'Test', '100')
        mounted = {str(self.library): ('100', '/dev/test1', False)}
        with patch('opldeck.library.devices.mounts', side_effect=lambda: mounted.copy()):
            window = MainWindow(auto_devices=False, cover_preloader=self.service)
            self.addCleanup(window.close)
            window.devices_changed((volume,))
            self.wait(lambda: window.worker is None and self.fetcher.busy)
            self.assertEqual(window.iso_table.rowCount(), 1)
            self.finish()
            self.assertIsNotNone(self.service.peek(self.key).download)
            mounted.clear()
            window.devices_changed(())
            self.assertFalse(self.service.cache)
            self.assertFalse((self.library / 'ART').exists())

    def test_remount_same_path_rejects_old_result_even_before_monitor_event(self):
        volume = Volume(self.library, '/dev/test1', 'Test', '100')
        mounted = {str(self.library): ('100', '/dev/test1', False)}
        with patch('opldeck.library.devices.mounts', side_effect=lambda: mounted.copy()):
            self.service.reset(volume)
            self.start_network()
            mounted[str(self.library)] = ('200', '/dev/other', False)
            self.finish()
            self.assertFalse(self.service.cache)
