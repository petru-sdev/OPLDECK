import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMessageBox
    from opldeck.library.devices import Volume, library_files, parse_volumes, volume_available
    from opldeck.ui.gui import MainWindow, device_rows, run_usb_batch
    from opldeck.ui.batch_gui import BatchDialog
    from opldeck.covers.cover_preload import CoverPreloader
except ModuleNotFoundError:
    QApplication = None

from opldeck.core.conversion import install_iso
from opldeck.core.formats import Media
from opldeck.core.ul import read_cfg
from tests.fixtures import make_iso


@unittest.skipIf(QApplication is None, 'PySide6 não instalado')
class DeviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.usb = self.root / 'USB Teste'
        self.usb.mkdir()
        self.volume = Volume(self.usb, '/dev/test1', 'USB Teste', '100')
        self.mounted = {str(self.usb): ('100', '/dev/test1', False)}
        mock_mounts = patch('opldeck.library.devices.mounts', side_effect=lambda: self.mounted.copy())
        mock_mounts.start()
        self.addCleanup(mock_mounts.stop)
        covers = CoverPreloader(settings=QSettings(str(self.root / 'covers.ini'), QSettings.Format.IniFormat))
        self.window = MainWindow(auto_devices=False, cover_preloader=covers)
        self.window.show()
        self.addCleanup(self.cleanup_window)
        self.source = make_iso(self.root / 'Local.iso', 'SLUS_111.11')

    def cleanup_window(self):
        self.window.pool.waitForDone(5000)
        self.app.processEvents()
        self.window.close()

    def wait(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if self.window.worker is None and not self.window.pending_volume:
                return
            time.sleep(.005)
        self.fail('Leitura do pendrive não terminou')

    def connect(self, volumes=None):
        self.window.device_monitor.changed.emit(tuple(volumes or [self.volume]), 0)
        self.wait()

    def iso_paths(self):
        table = self.window.iso_table
        return {table.item(r, 6).text() for r in range(table.rowCount())}

    def installed(self):
        game = make_iso(self.root / 'Installed.iso', 'SLUS_222.22')
        install_iso(game, self.usb, media=Media.DVD)
        return game

    def test_parser_inherits_usb_filters_internal_and_requires_actual_mount(self):
        nodes = {'blockdevices': [
            {'type': 'disk', 'path': '/dev/test', 'tran': 'usb', 'children': [
                {'type': 'part', 'path': '/dev/test1', 'mountpoints': [str(self.usb)], 'label': 'Games'}]},
            {'type': 'disk', 'path': '/dev/nvme1', 'tran': 'nvme', 'mountpoints': ['/internal']},
            {'type': 'disk', 'path': '/dev/no_mount', 'tran': 'usb', 'mountpoints': ['/stale']},
        ]}
        volumes, unmounted = parse_volumes(nodes, {**self.mounted, '/internal': ('1', '/dev/nvme1', False)})
        self.assertEqual(len(volumes), 1)
        self.assertEqual(volumes[0].key, self.volume.key)
        self.assertEqual(unmounted, 1)

    def test_identity_rejects_remount_even_when_directory_still_exists(self):
        self.assertTrue(volume_available(self.volume, write=True))
        self.mounted[str(self.usb)] = ('200', '/dev/test1', False)
        self.assertFalse(volume_available(self.volume))
        self.mounted.clear()
        self.assertTrue(self.usb.is_dir())
        self.assertFalse(volume_available(self.volume))

    def test_scanner_ignores_trash_nested_files_and_links(self):
        for name in ('CD', 'DVD', '.Trash-1000'):
            (self.usb / name).mkdir()
            make_iso(self.usb / name / 'Game.ISO')
        make_iso(self.usb / 'Root.iso')
        (self.usb / 'linked.iso').symlink_to(self.source)
        cfg, paths = library_files(self.usb)
        self.assertIsNone(cfg)
        self.assertEqual(len(paths), 3)
        self.assertFalse(any('Trash' in p or 'linked' in p for p in paths))

    def test_startup_or_insert_loads_ul_and_switches_tab(self):
        self.installed()
        self.window.device_monitor.changed.emit((self.volume,), 0)
        self.assertTrue(self.window.device_animation.isActive())
        self.assertFalse(self.window.usb_start.isEnabled())
        self.wait()
        self.assertEqual(self.window.ul_table.rowCount(), 1)
        self.assertEqual(self.window.cfg_path, self.usb / 'ul.cfg')
        self.assertIs(self.window.active_table(), self.window.ul_table)
        self.assertFalse(self.window.device_animation.isActive())

    def test_local_iso_survives_auto_scan_and_unlocks_start_with_destination(self):
        self.window.load_isos([str(self.source)])
        self.wait()
        self.assertFalse(self.window.usb_start.isEnabled())
        (self.usb / 'DVD').mkdir()
        remote = make_iso(self.usb / 'DVD' / 'Already.iso', 'SLUS_333.33')
        self.installed()
        self.connect()
        self.assertEqual(self.iso_paths(), {str(self.source), str(remote)})
        self.assertIs(self.window.active_table(), self.window.iso_table)
        self.assertTrue(self.window.usb_start.isEnabled())
        self.assertEqual([row[6] for row in self.window.usb_values()], [str(self.source)])
        observed = []

        def review(dialog):
            observed.append(dialog.destination.text())
            dialog.reject()
            return dialog.result()

        with patch.object(BatchDialog, 'exec', review):
            self.window.usb_start.click()
        self.assertEqual(observed, [str(self.usb)])

    def test_empty_pendrive_is_valid_destination_without_cfg(self):
        install_iso(self.source, self.root, media=Media.DVD)
        self.window.load_cfg(self.root / 'ul.cfg')
        self.wait()
        self.assertEqual(self.window.ul_table.rowCount(), 1)
        self.window.load_isos([str(self.source)])
        self.wait()
        self.connect()
        self.assertTrue(self.window.usb_start.isEnabled())
        self.assertFalse((self.usb / 'ul.cfg').exists())
        self.assertEqual(self.window.ul_table.rowCount(), 0)
        self.assertIsNone(self.window.cfg_path)

    def test_readonly_device_still_lists_games_and_blocks_start(self):
        self.installed()
        self.mounted[str(self.usb)] = ('100', '/dev/test1', True)
        self.window.load_isos([str(self.source)])
        self.wait()
        self.connect()
        self.assertEqual(self.window.ul_table.rowCount(), 1)
        self.assertFalse(self.window.usb_start.isEnabled())
        self.assertIn('Somente leitura', self.window.device_status.text())

    def test_corrupt_index_blocks_start_but_keeps_local_isos(self):
        (self.usb / 'ul.cfg').write_bytes(b'broken')
        self.window.load_isos([str(self.source)])
        self.wait()
        self.connect()
        self.assertIn('Falha em ul.cfg', self.window.device_status.text())
        self.assertFalse(self.window.usb_start.isEnabled())
        self.assertEqual(self.iso_paths(), {str(self.source)})

    def test_remove_clears_device_rows_only_and_disables_start(self):
        self.installed()
        make_iso(self.usb / 'Remote.iso')
        self.window.load_isos([str(self.source)])
        self.wait()
        self.connect()
        self.mounted.clear()
        self.window.device_monitor.changed.emit((), 0)
        self.wait()
        self.assertEqual(self.iso_paths(), {str(self.source)})
        self.assertEqual(self.window.ul_table.rowCount(), 0)
        self.assertIsNone(self.window.cfg_path)
        self.assertFalse(self.window.usb_start.isEnabled())

    def test_multiple_devices_require_choice_and_do_not_steal_selected_destination(self):
        other_root = self.root / 'Other'
        other_root.mkdir()
        other = Volume(other_root, '/dev/test2', 'Other', '101')
        self.mounted[str(other_root)] = ('101', '/dev/test2', False)
        self.connect([self.volume, other])
        self.assertIsNone(self.window.active_volume)
        self.window.device_combo.setCurrentIndex(2)
        self.wait()
        self.assertEqual(self.window.active_volume, other)
        self.window.devices_changed((other, self.volume))
        self.assertEqual(self.window.active_volume, other)

    def test_removal_during_scan_ignores_result(self):
        result = device_rows(self.volume)
        from threading import Event
        released = Event()
        def delayed(volume):
            released.wait(3)
            return result
        with patch('opldeck.ui.gui.device_rows', side_effect=delayed):
            self.window.devices_changed((self.volume,))
            self.mounted.clear()
            self.window.devices_changed(())
            released.set()
            self.wait()
        self.assertFalse(self.window.volume_ready)
        self.assertIsNone(self.window.cfg_path)

    def test_insert_during_local_scan_is_queued(self):
        self.installed()
        self.window.load_isos([str(self.source)])
        self.window.devices_changed((self.volume,))
        self.assertTrue(self.window.pending_volume)
        self.wait()
        self.assertIn(str(self.source), self.iso_paths())
        self.assertEqual(self.window.ul_table.rowCount(), 1)

    def test_rescan_does_not_duplicate_images(self):
        make_iso(self.usb / 'Remote.iso')
        self.connect()
        self.window.rescan_device()
        self.wait()
        self.assertEqual(self.window.iso_table.rowCount(), 1)

    def test_review_blocks_removed_or_replaced_destination(self):
        from opldeck.ui.gui import iso_rows
        dialog = BatchDialog(iso_rows([str(self.source)]), 'install', volume=self.volume)
        self.addCleanup(dialog.deleteLater)
        dialog.media[0].setCurrentText('DVD')
        self.assertTrue(dialog.start_button.isEnabled())
        self.mounted[str(self.usb)] = ('101', '/dev/other', False)
        self.assertFalse(dialog.validate())
        self.assertFalse(dialog.start_button.isEnabled())

    def test_start_transfers_to_detected_destination_after_review(self):
        self.window.load_isos([str(self.source)])
        self.wait()
        self.connect()
        def approve(dialog):
            dialog.media[0].setCurrentText('DVD')
            dialog.accept()
            return dialog.result()
        with patch.object(BatchDialog, 'exec', approve):
            self.window.start_usb_install()
            self.wait()
        self.assertEqual(read_cfg(self.usb / 'ul.cfg')[0].game_id, 'SLUS_111.11')
        self.assertTrue(self.source.exists())

    def test_worker_refuses_stale_mount_before_any_write(self):
        from threading import Event
        from opldeck.core.batch import TransferJob
        job = TransferJob('install', self.source, self.usb, 'SLUS_111.11', 'Local', self.source.stat().st_size, Media.DVD)
        self.mounted.clear()
        with self.assertRaisesRegex(ValueError, 'removido'):
            run_usb_batch((job,), self.volume, cancel=Event(), progress=lambda event: None)
        self.assertEqual(list(self.usb.iterdir()), [])

    def test_removal_during_transfer_requests_cancel(self):
        from opldeck.core.batch import TransferJob, BatchResult
        job = TransferJob('install', self.source, self.usb, 'SLUS_111.11', 'Local', self.source.stat().st_size, Media.DVD)
        self.connect()
        def transferring(jobs, volume, *, cancel, progress):
            self.assertTrue(cancel.wait(3))
            return BatchResult(jobs, (), 0, 'Conversão cancelada', True)
        with patch('opldeck.ui.gui.run_usb_batch', side_effect=transferring):
            self.window.start_batch((job,), volume=self.volume)
            self.mounted.clear()
            self.window.devices_changed(())
            self.wait()
        self.assertIn('cancelado', self.window.batch_label.text())
        self.assertFalse(self.window.usb_start.isEnabled())
        self.assertEqual(list(self.usb.iterdir()), [])

    def test_discovery_failure_disables_previously_ready_destination(self):
        self.window.load_isos([str(self.source)])
        self.wait()
        self.connect()
        self.assertTrue(self.window.usb_start.isEnabled())
        self.window.device_monitor.failed.emit('Falha na descoberta')
        self.assertFalse(self.window.usb_start.isEnabled())
        self.assertEqual(self.window.device_status.text(), 'Falha na descoberta')
