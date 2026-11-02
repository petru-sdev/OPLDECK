import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from collections import namedtuple
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from opldeck.ui.batch_gui import BatchDialog
    from opldeck.ui.gui import InstallDialog, MainWindow, iso_rows
    from opldeck.library.devices import Volume
    from opldeck.covers.cover_preload import CoverPreloader
except ModuleNotFoundError:
    QApplication = None

from opldeck.core.batch import TransferJob
from opldeck.core.formats import CD_SIZE_THRESHOLD, Media
from opldeck.core.iso import inspect_iso
from opldeck.core.ul import read_cfg
from tests.fixtures import make_iso


@unittest.skipIf(QApplication is None, 'PySide6 não instalado')
class Final080GUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / 'usb'
        self.destination.mkdir()
        covers = CoverPreloader(settings=QSettings(str(self.root / 'covers.ini'), QSettings.Format.IniFormat))
        self.window = MainWindow(auto_devices=False, cover_preloader=covers)
        self.window.show()
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        self.window.pool.waitForDone(5000)
        self.app.processEvents()
        self.window.close()

    def wait_worker(self):
        deadline = time.monotonic() + 5
        while self.window.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.window.worker)

    def import_zip(self, count=1):
        archive = self.root / 'games.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as target:
            for index in range(count):
                iso = make_iso(self.root / f'Game{index}.iso', f'SLUS_100.{index:02}')
                target.write(iso, f'DVD/{iso.name}')
        self.window.start_archives([str(archive)], self.root)
        self.assertTrue(self.window.progress_bar.extraction)
        self.assertFalse(self.window.overall_progress.extraction)
        self.wait_worker()
        return archive

    def test_zip_to_install_five_games_total_continues_from_half(self):
        archive = self.import_zip(5)
        self.assertTrue(archive.is_file())
        self.assertEqual(self.window.iso_table.rowCount(), 5)
        self.assertEqual(self.window.overall_progress.value(), 500)
        self.window.iso_table.selectAll()
        dialog = BatchDialog(self.window._selected_values(self.window.iso_table), 'install')
        self.addCleanup(dialog.deleteLater)
        dialog.destination.setText(str(self.destination))
        dialog.auto_media.setChecked(True)
        jobs = dialog.jobs()
        observed = []
        original = self.window._batch_progress
        def report(event):
            original(event)
            observed.append(self.window.overall_progress.value())
        with patch.object(self.window, '_batch_progress', report):
            self.window.start_batch(jobs)
            self.assertEqual(self.window.overall_progress.value(), 500)
            self.assertFalse(self.window.progress_bar.extraction)
            self.wait_worker()
        self.assertEqual(self.window.overall_progress.value(), 1000)
        self.assertEqual(observed, sorted(observed))
        self.assertGreaterEqual(min(observed), 500)
        self.assertEqual(len(read_cfg(self.destination / 'ul.cfg')), 5)
        self.assertTrue(all(job.source.is_file() for job in jobs))

    def test_single_zip_install_and_changed_source_does_not_receive_credit(self):
        self.import_zip()
        source = Path(self.window.iso_table.item(0, 6).text())
        self.assertEqual(self.window.extraction_credit(source), source.stat().st_size)
        self.window.start_install(source, self.destination, 'Game0', Media.DVD)
        self.assertEqual(self.window.overall_progress.value(), 500)
        self.wait_worker()
        self.assertEqual(self.window.overall_progress.value(), 1000)
        with patch('opldeck.ui.gui.QMessageBox.warning'):
            self.window.start_install(source, self.destination, 'Game0', Media.DVD)
            self.wait_worker()
        self.assertEqual(self.window.overall_progress.value(), 500)
        self.assertTrue(source.is_file())
        with source.open('ab') as stream:
            stream.write(b'changed')
        self.assertEqual(self.window.extraction_credit(source), 0)

    def test_bad_zip_keeps_existing_iso_list_and_stops_indicators(self):
        iso = make_iso(self.root / 'Local.iso')
        self.window.load_isos([str(iso)])
        self.wait_worker()
        archive = self.root / 'bad.zip'
        archive.write_bytes(b'broken')
        with patch('opldeck.ui.gui.QMessageBox.warning'):
            self.window.start_archives([str(archive)], self.root)
            self.wait_worker()
        self.assertEqual(self.window.iso_table.rowCount(), 1)
        self.assertFalse(self.window.activity.timer.isActive())
        self.assertEqual(self.window.overall_progress.value(), 0)
        self.assertEqual(list(self.root.glob('OPLDeck-extraidos-*')), [])

    def test_media_cut_boundaries_are_estimates_and_folder_has_priority(self):
        self.assertEqual(CD_SIZE_THRESHOLD, 734068736)
        for size, expected in [(CD_SIZE_THRESHOLD - 1, Media.CD),
                               (CD_SIZE_THRESHOLD, Media.DVD), (CD_SIZE_THRESHOLD + 1, Media.DVD)]:
            with self.subTest(size=size):
                path = make_iso(self.root / 'Boundary.iso')
                with path.open('r+b') as stream:
                    stream.truncate(size)
                game = inspect_iso(path)
                self.assertEqual(game.media, expected)
                self.assertTrue(game.media_estimated)
                dialog = InstallDialog(game)
                self.addCleanup(dialog.deleteLater)
                dialog.media_combo.setCurrentText('DVD')
                dialog.auto_media.setChecked(True)
                self.assertEqual(dialog.media_combo.currentText(), expected.name)
                self.assertFalse(dialog.media_combo.isEnabled())
                dialog.auto_media.setChecked(False)
                self.assertEqual(dialog.media_combo.currentText(), 'DVD')
                self.assertTrue(dialog.media_combo.isEnabled())
        folder = self.root / 'DVD'
        folder.mkdir()
        small = inspect_iso(make_iso(folder / 'Small.iso'))
        self.assertEqual(small.media, Media.DVD)
        self.assertFalse(small.media_estimated)

    def test_batch_automatic_optional_restores_manual_and_warns_estimate(self):
        iso = make_iso(self.root / 'Local.iso')
        dialog = BatchDialog(iso_rows([str(iso)]), 'install')
        self.addCleanup(dialog.deleteLater)
        dialog.destination.setText(str(self.destination))
        self.assertFalse(dialog.auto_media.isChecked())
        self.assertFalse(dialog.start_button.isEnabled())
        dialog.auto_media.setChecked(True)
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertEqual(dialog.jobs()[0].media, Media.CD)
        self.assertIn('estimativa', dialog.media_hint.text())
        self.assertIn('Estimativa', dialog.media[0].toolTip())
        dialog.auto_media.setChecked(False)
        self.assertFalse(dialog.start_button.isEnabled())
        dialog.media[0].setCurrentText('DVD')
        self.assertEqual(dialog.jobs()[0].media, Media.DVD)
        dialog.auto_media.setChecked(True)
        dialog.auto_media.setChecked(False)
        self.assertEqual(dialog.jobs()[0].media, Media.DVD)

    def test_storage_values_visual_bar_and_stale_mount(self):
        usage = namedtuple('Usage', 'total used free')(128 << 30, 33 << 30, 95 << 30)
        old = Volume(self.destination, '/dev/test', 'Test', '1')
        new = Volume(self.destination, '/dev/test', 'Test', '2')
        self.window.active_volume = old
        with patch('opldeck.ui.gui.volume_available', return_value=True):
            self.window.storage_finished(old, usage, '')
        self.assertFalse(self.window.storage_bar.isTextVisible())
        self.assertEqual(self.window.storage_bar.value(), 33 * 1000 // 128)
        self.assertIn('95.00 GiB livres', self.window.storage_label.text())
        self.assertIn('33.00 GiB usados', self.window.storage_label.text())
        self.assertIn('128.00 GiB no total', self.window.storage_label.text())
        self.window.active_volume = new
        self.window.clear_storage()
        self.window.storage_finished(old, usage, '')
        self.assertFalse(self.window.storage_bar.isEnabled())
        self.assertNotIn('95.00', self.window.storage_label.text())
        self.window.active_volume = None

    def test_storage_refresh_runs_off_ui_thread(self):
        usage = namedtuple('Usage', 'total used free')(100, 25, 75)
        volume = Volume(self.destination, '/dev/test', 'Test', '1')
        self.window.active_volume = volume
        with patch('opldeck.ui.gui.volume_usage', return_value=usage), patch('opldeck.ui.gui.volume_available', return_value=True):
            self.window.refresh_storage()
            self.assertTrue(self.window.pool.waitForDone(5000))
            self.app.processEvents()
        self.assertEqual(self.window.storage_bar.value(), 250)
        self.window.active_volume = None
