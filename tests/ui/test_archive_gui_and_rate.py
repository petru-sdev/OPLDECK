import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from opldeck.ui.archive_gui import ArchivePreview, ProbeRequest
from opldeck.library.archives import list_archive
from opldeck.core.batch import TransferJob
from opldeck.covers.cover_preload import CoverPreloader
from opldeck.core.formats import Media
from opldeck.ui.gui import MainWindow
from opldeck.library.import_folder import ImportPreferences
from opldeck.core.transfer_rate import TransferRate
from tests.fixtures import make_iso, make_bin


class RateTests(unittest.TestCase):
    def test_eta_uses_last_thirty_seconds(self):
        rate = TransferRate()
        for second in range(61):
            completed = min(second, 30) * 100 + max(0, second - 30) * 10
            speed, eta, _ = rate.estimate(second, completed, 4000)
        self.assertEqual(speed, 10)
        self.assertEqual(eta, 70)

    def test_speed_drop_and_no_progress_are_visible(self):
        rate = TransferRate()
        for second in range(31):
            speed, eta, dropped = rate.estimate(second, min(second, 20) * 100, 5000)
        self.assertTrue(dropped)
        for second in range(31, 61):
            speed, eta, _ = rate.estimate(second, 2000, 5000)
        self.assertEqual(speed, 0)
        self.assertIsNone(eta)

    def test_new_item_resets_previous_samples(self):
        rate = TransferRate()
        rate.estimate(0, 0, 1000)
        rate.estimate(10, 1000, 1000)
        rate.estimate(11, 0, 2000)
        speed, eta, dropped = rate.estimate(21, 100, 2000)
        self.assertEqual(speed, 10)
        self.assertEqual(eta, 190)
        self.assertFalse(dropped)


class ArchiveFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        environment = patch.dict(os.environ, {'OPLDECK_IMPORT_CACHE': str(self.root / 'cache')})
        environment.start()
        self.addCleanup(environment.stop)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.settings.setValue('import/cleanup', 'always')
        self.window = MainWindow(auto_devices=False, cover_preloader=CoverPreloader(settings=self.settings))
        self.window.import_monitor.timer.stop()
        self.addCleanup(self.close)
        warning = patch('opldeck.ui.gui.QMessageBox.warning')
        self.warning = warning.start()
        self.addCleanup(warning.stop)
        iso = make_iso(self.root / 'Game.iso')
        self.archive = self.root / 'game.download'
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.write(iso, 'Game.iso')
            archive.writestr('readme.html', 'ignored')

    def close(self):
        self.window.pool.waitForDone(5000)
        self.app.processEvents()
        self.window.close()

    def wait(self):
        deadline = time.monotonic() + 5
        while (self.window.worker or self.window.archive_import.active) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.window.worker)
        self.assertFalse(self.window.archive_import.active)

    def test_preview_lists_all_and_selects_only_games(self):
        plan = list_archive(self.archive)
        dialog = ArchivePreview([plan])
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.selected_plans()[0].selected, ('Game.iso',))
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(dialog.start.isEnabled())
        self.assertEqual(dialog.selected_plans(), [])

    def test_manual_flow_uses_local_temporary_and_cleans_after_transfer(self):
        with patch.object(ArchivePreview, 'exec', return_value=QDialog.DialogCode.Accepted):
            self.window.archive_import.start([self.archive])
            self.wait()
        self.assertFalse(self.warning.called, self.warning.call_args)
        self.assertEqual(self.window.iso_table.rowCount(), 1)
        source = Path(self.window.iso_table.item(0, 6).text())
        self.assertTrue(source.is_relative_to(self.root / 'cache'))
        destination = self.root / 'usb'
        destination.mkdir()
        events = []
        original = self.window._progress
        def progress(event):
            original(event)
            current = getattr(event, 'current', event)
            if getattr(current, 'phase', '') == 'sync':
                events.append(self.window.activity.message.text())
        with patch.object(self.window, '_progress', progress):
            self.window.start_batch([TransferJob('install', source, destination, 'SLUS_123.45', 'Game', source.stat().st_size, Media.DVD)])
            self.wait()
        self.assertTrue(events)
        self.assertFalse(source.exists())
        self.assertTrue(self.archive.exists())
        self.assertEqual(self.window.overall_progress.value(), 1000)

    def test_preview_cancel_creates_no_extracted_files(self):
        with patch.object(ArchivePreview, 'exec', return_value=QDialog.DialogCode.Rejected):
            self.window.archive_import.start([self.archive])
            self.wait()
        self.assertFalse((self.root / 'cache').exists())
        self.assertEqual(self.window.iso_table.rowCount(), 0)

    def test_bin_cue_selection_remains_visibly_paired(self):
        binary = make_bin(self.root / 'Game.iso', self.root / 'Game.bin', 1)
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.write(binary, 'Game.bin')
            archive.writestr('Game.cue', 'FILE "Game.bin" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n')
        dialog = ArchivePreview([list_archive(self.archive)])
        self.addCleanup(dialog.deleteLater)
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dialog.table.item(1, 0).checkState(), Qt.CheckState.Unchecked)
        self.assertFalse(dialog.start.isEnabled())
        dialog.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog.table.item(0, 0).checkState(), Qt.CheckState.Checked)
        self.assertEqual(len(dialog.selected_plans()[0].selected), 2)

    def test_ask_cleanup_no_preserves_extracted_files(self):
        self.settings.setValue('import/cleanup', 'ask')
        with patch('opldeck.ui.gui.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            self.assertFalse(self.window.import_cleanup_choice([{}]))
        with patch('opldeck.ui.gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.window.import_cleanup_choice([{}]))

    def test_password_request_is_not_exposed_by_representation(self):
        self.assertNotIn('secret-value', repr(ProbeRequest(self.archive, 'secret-value')))

    def test_error_during_listing_allows_next_archive(self):
        bad = self.root / 'bad.data'
        bad.write_bytes(b'PK\x03\x04broken')
        with patch.object(ArchivePreview, 'exec', return_value=QDialog.DialogCode.Accepted):
            self.window.archive_import.start([bad, self.archive])
            self.wait()
        self.assertEqual(self.window.iso_table.rowCount(), 1)
        self.assertTrue(self.warning.called)
