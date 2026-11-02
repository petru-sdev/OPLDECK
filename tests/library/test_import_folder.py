import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch
import zipfile

from opldeck.core.batch import TransferJob, run_batch
from opldeck.core.formats import Media
from opldeck.library.import_folder import (
    ImportPreferences, checked_record, discover, finish_imports, prepare_archive, snapshot,
)
from opldeck.core.iso import inspect_iso
from opldeck.core.ul import read_cfg
from tests.fixtures import make_iso

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from opldeck.covers.cover_preload import CoverPreloader
    from opldeck.ui.gui import MainWindow, transfer_with_import_cleanup
    from opldeck.ui.settings_gui import SettingsDialog
    from opldeck.ui.themes import ThemeManager
except ModuleNotFoundError:
    QApplication = None


class ImportFolderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        cache = patch.dict(os.environ, {'OPLDECK_IMPORT_CACHE': str(self.root / 'cache')})
        cache.start()
        self.addCleanup(cache.stop)
        self.folder = self.root / 'Downloads' / 'Import'
        self.folder.mkdir(parents=True)
        self.destination = self.root / 'usb'
        self.destination.mkdir()
        self.preferences = ImportPreferences(str(self.folder), True, 'always')

    def archive(self, count=1, name='games.zip', start=0):
        path = self.folder / name
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as output:
            for index in range(start, start + count):
                iso = make_iso(self.root / f'Game{index}.iso', f'SLUS_100.{index:02}')
                output.write(iso, f'DVD/{iso.name}')
        return path

    def prepare(self, count=1, **kwargs):
        archive = self.archive(count, **kwargs)
        result = prepare_archive(archive, snapshot(archive), self.preferences)
        jobs = tuple(TransferJob('install', path, self.destination, inspect_iso(path).game_id,
                                 path.stem, path.stat().st_size, Media.DVD) for path in result.archive_result.paths)
        return archive, result, jobs

    def install(self, jobs):
        result = run_batch(jobs)
        self.assertFalse(result.error, result.error)
        return result

    def test_discovery_direct_zips_only_ignores_partial_and_symlink(self):
        good = self.archive(name='ready.ZIP')
        partial = self.archive(name='waiting.zip')
        Path(str(partial) + '.part').touch()
        (self.folder / 'link.zip').symlink_to(good)
        (self.folder / 'unrelated.txt').write_text('keep')
        nested = self.folder / 'nested'
        nested.mkdir()
        (nested / 'hidden.zip').write_bytes(good.read_bytes())
        _, candidates, records, errors = discover(self.folder)
        self.assertEqual(set(candidates), {str(good)})
        self.assertFalse(records or errors)

    def test_prepare_persists_resume_and_prevents_duplicate_extraction(self):
        archive, result, jobs = self.prepare(2)
        _, candidates, records, errors = discover(self.folder)
        self.assertEqual(candidates, {})
        self.assertFalse(errors)
        self.assertEqual(len(records), 1)
        self.assertEqual(set(records[0]['files']), {str(job.source) for job in jobs})
        self.assertTrue(archive.exists())
        self.assertTrue(all(job.source.exists() for job in jobs))

    def test_success_cleans_only_extracted_files_and_keeps_zip_by_default(self):
        archive, prepared, jobs = self.prepare(2)
        unrelated = prepared.archive_result.directory / 'keep.txt'
        unrelated.write_text('keep')
        self.install(jobs)
        removed, warnings = finish_imports([prepared.record], jobs, delete_extracted=True)
        self.assertFalse(warnings, warnings)
        self.assertEqual(set(removed), {str(job.source) for job in jobs})
        self.assertTrue(archive.exists())
        self.assertEqual(unrelated.read_text(), 'keep')
        self.assertEqual(len(read_cfg(self.destination / 'ul.cfg')), 2)
        self.assertEqual(discover(self.folder)[1], {})

    def test_cleanup_only_removes_temporaries_after_verified_transfer(self):
        archive, prepared, jobs = self.prepare()
        self.install(jobs)
        removed, warnings = finish_imports([prepared.record], jobs, delete_extracted=True)
        self.assertFalse(warnings, warnings)
        self.assertTrue(archive.exists())
        self.assertFalse(prepared.archive_result.directory.exists())
        self.assertEqual(len(removed), 1)

    def test_partial_selection_waits_until_all_games_are_transferred(self):
        archive, prepared, jobs = self.prepare(2)
        self.install(jobs[:1])
        self.assertEqual(finish_imports([prepared.record], jobs[:1]), ((), ()))
        self.assertTrue(all(job.source.exists() for job in jobs))
        self.install(jobs[1:])
        removed, warnings = finish_imports([prepared.record], jobs[1:], delete_extracted=True)
        self.assertFalse(warnings, warnings)
        self.assertEqual(len(removed), 2)
        self.assertTrue(archive.exists())

    def test_corrupt_destination_preserves_all_sources(self):
        archive, prepared, jobs = self.prepare()
        self.install(jobs)
        part = self.destination / read_cfg(self.destination / 'ul.cfg')[0].part_name(0)
        with part.open('r+b') as stream:
            stream.write(b'bad')
        removed, warnings = finish_imports([prepared.record], jobs, delete_extracted=True)
        self.assertFalse(removed)
        self.assertIn('conteúdo diferente', warnings[0])
        self.assertTrue(archive.exists() and jobs[0].source.exists())

    def test_failure_verifying_second_archive_keeps_first_archive_too(self):
        first, prepared_a, a = self.prepare(name='a.zip', start=0)
        second, prepared_b, b = self.prepare(name='b.zip', start=1)
        self.install(a + b)
        part = self.destination / read_cfg(self.destination / 'ul.cfg')[1].part_name(0)
        part.write_bytes(b'broken')
        removed, warnings = finish_imports([prepared_a.record, prepared_b.record], a + b, delete_extracted=True)
        self.assertTrue(warnings)
        self.assertFalse(removed)
        self.assertTrue(first.exists() and second.exists())
        self.assertTrue(all(job.source.exists() for job in a + b))

    def test_changed_archive_preserved_even_after_success(self):
        archive, prepared, jobs = self.prepare()
        self.install(jobs)
        with archive.open('ab') as stream:
            stream.write(b'changed')
        removed, warnings = finish_imports([prepared.record], jobs, delete_extracted=True)
        self.assertFalse(removed)
        self.assertTrue(warnings)
        self.assertTrue(archive.exists() and jobs[0].source.exists())

    def test_changed_extracted_file_preserved(self):
        archive, prepared, jobs = self.prepare()
        self.install(jobs)
        with jobs[0].source.open('ab') as stream:
            stream.write(b'changed')
        removed, warnings = finish_imports([prepared.record], jobs, delete_extracted=True)
        self.assertFalse(removed)
        self.assertTrue(warnings)
        self.assertTrue(archive.exists() and jobs[0].source.exists())

    def test_cancel_verification_preserves_sources(self):
        archive, prepared, jobs = self.prepare()
        self.install(jobs)
        cancelled = Event()
        cancelled.set()
        removed, warnings = finish_imports([prepared.record], jobs, cancel=cancelled, delete_extracted=True)
        self.assertFalse(removed)
        self.assertTrue(warnings)
        self.assertTrue(archive.exists() and jobs[0].source.exists())

    def test_removed_volume_prevents_cleanup(self):
        archive, prepared, jobs = self.prepare()
        self.install(jobs)
        def missing():
            raise OSError('Pendrive removido')
        removed, warnings = finish_imports([prepared.record], jobs, guard=missing, delete_extracted=True)
        self.assertFalse(removed)
        self.assertIn('Pendrive removido', warnings[0])
        self.assertTrue(archive.exists() and jobs[0].source.exists())

    def test_source_change_before_extraction_is_rejected(self):
        archive = self.archive()
        expected = snapshot(archive)
        with archive.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaises(ValueError):
            prepare_archive(archive, expected, self.preferences)
        self.assertFalse((self.root / 'cache').exists())

    def test_corrupt_or_path_traversal_zip_keeps_original(self):
        for name, invalid in [('broken.zip', True), ('traversal.zip', False)]:
            archive = self.folder / name
            if invalid:
                archive.write_bytes(b'incomplete')
            else:
                with zipfile.ZipFile(archive, 'w') as output:
                    output.writestr('../escape.iso', b'bad')
            with self.assertRaises((ValueError, zipfile.BadZipFile)):
                prepare_archive(archive, snapshot(archive), self.preferences)
            self.assertTrue(archive.exists())
        self.assertFalse((self.folder / 'escape.iso').exists())

    def test_manifest_cannot_target_external_file(self):
        archive, prepared, jobs = self.prepare()
        path = Path(prepared.record['manifest'])
        data = json.loads(path.read_text())
        outside = make_iso(self.root / 'outside.iso')
        data['files'] = {str(outside): snapshot(outside)}
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            checked_record(path, self.folder)
        self.assertTrue(discover(self.folder)[3])
        self.assertTrue(outside.exists())

    def test_symlinked_state_directory_refused(self):
        external = self.root / 'external'
        external.mkdir()
        (self.root / 'cache').symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            discover(self.folder)
        archive = self.archive()
        with self.assertRaises(ValueError):
            prepare_archive(archive, snapshot(archive), self.preferences)
        self.assertEqual(list(external.iterdir()), [])

    def test_symlink_replacing_extracted_iso_blocks_cleanup(self):
        archive, prepared, jobs = self.prepare()
        self.install(jobs)
        source = jobs[0].source
        other = self.root / 'other.iso'
        other.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(other)
        removed, warnings = finish_imports([prepared.record], jobs, delete_extracted=True)
        self.assertFalse(removed)
        self.assertTrue(warnings)
        self.assertTrue(archive.exists() and other.exists())

    def test_cleanup_failure_retains_zip_and_does_not_repeat_import(self):
        archive, prepared, jobs = self.prepare()
        self.install(jobs)
        unlink = Path.unlink
        def fail(path, *args, **kwargs):
            if path == jobs[0].source:
                raise PermissionError('Sem permissão para limpeza')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail):
            removed, warnings = finish_imports([prepared.record], jobs, delete_extracted=True)
        self.assertFalse(removed)
        self.assertTrue(warnings)
        self.assertTrue(archive.exists() and jobs[0].source.exists())
        self.assertEqual(discover(self.folder)[1], {})


@unittest.skipIf(QApplication is None, 'PySide6 não instalado')
class ImportFolderGUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp_files = ImportFolderTests.setUp
    archive = ImportFolderTests.archive

    def setUp(self):
        self.setUp_files()
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        for key, value in self.preferences.values().items():
            self.settings.setValue(key, value)
        self.settings.sync()
        self.covers = CoverPreloader(settings=self.settings)
        self.window = MainWindow(auto_devices=False, cover_preloader=self.covers)
        self.window.import_monitor.timer.stop()
        warning = patch('opldeck.ui.gui.QMessageBox.warning')
        self.warning = warning.start()
        self.addCleanup(warning.stop)
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        self.window.pool.waitForDone(5000)
        self.app.processEvents()
        self.window.close()

    def wait(self):
        deadline = time.monotonic() + 5
        while (self.window.worker or self.window.import_monitor.worker) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.window.worker)
        self.assertIsNone(self.window.import_monitor.worker)

    def detected(self, count=1):
        archive = self.archive(count)
        old = time.time() - 30
        os.utime(archive, (old, old))
        self.window.import_monitor.poll()
        self.wait()
        self.assertEqual(self.window.iso_table.rowCount(), 0)
        self.window.import_monitor.poll()
        self.wait()
        return archive

    def jobs(self):
        return tuple(TransferJob('install', Path(self.window.iso_table.item(row, 6).text()), self.destination,
                                 self.window.iso_table.item(row, 1).text(), self.window.iso_table.item(row, 2).text(),
                                 int(self.window.iso_table.item(row, 7).text()), Media.DVD)
                     for row in range(self.window.iso_table.rowCount()))

    def test_detect_extract_transfer_cleanup_and_no_repeat(self):
        archive = self.detected(2)
        self.assertEqual(self.window.iso_table.rowCount(), 2)
        jobs = self.jobs()
        self.window.start_batch(jobs)
        self.wait()
        self.assertFalse(self.warning.called, self.warning.call_args)
        self.assertEqual(self.window.iso_table.rowCount(), 0)
        self.assertTrue(archive.exists())
        self.assertTrue(all(not job.source.exists() for job in jobs))
        self.window.import_monitor.poll()
        self.wait()
        self.assertEqual(self.window.iso_table.rowCount(), 0)

    def test_single_install_cleans_registered_iso_only(self):
        archive = self.detected()
        job = self.jobs()[0]
        self.window.start_install(job.source, job.destination, job.title, job.media)
        self.wait()
        self.assertFalse(job.source.exists())
        self.assertTrue(archive.exists())
        self.assertEqual(self.window.iso_table.rowCount(), 0)

    def test_cleanup_after_import_preserves_original_archive(self):
        archive = self.detected()
        self.settings.setValue('import/cleanup', 'always')
        self.settings.sync()
        self.window.start_batch(self.jobs())
        self.wait()
        self.assertFalse(self.warning.called, self.warning.call_args)
        self.assertTrue(archive.exists())

    def test_never_cleanup_preserves_sources(self):
        archive = self.detected()
        jobs = self.jobs()
        self.settings.setValue('import/cleanup', 'never')
        self.settings.sync()
        self.window.start_batch(jobs)
        self.wait()
        self.assertTrue(archive.exists() and jobs[0].source.exists())

    def test_restart_resumes_queue_without_reextracting(self):
        archive = self.detected()
        source = self.jobs()[0].source
        self.window.import_monitor.loaded.clear()
        self.window.imported.clear()
        self.window.iso_table.setRowCount(0)
        self.window.import_monitor.reset()
        self.window.import_monitor.poll()
        self.wait()
        self.assertEqual(self.jobs()[0].source, source)
        self.assertEqual(len(list((self.root / 'cache').glob('task-*'))), 1)
        self.assertTrue(archive.exists())

    def test_disabled_monitor_never_extracts(self):
        self.settings.setValue('import/enabled', False)
        self.settings.sync()
        self.archive()
        self.window.import_monitor.poll()
        self.assertIsNone(self.window.import_monitor.worker)
        self.assertFalse((self.root / 'cache').exists())

    def test_search_imports_button_tracks_folder_and_imports_on_click(self):
        button = self.window.import_search_button
        monitor = self.window.import_monitor
        row = self.window.table_pages[self.window.iso_table].layout().itemAt(1).layout()
        self.assertIs(row.itemAt(row.indexOf(self.window.zip_button) + 1).widget(), button)
        monitor.poll()
        self.wait()
        self.assertFalse(button.isEnabled())
        archive = self.archive()
        old = time.time() - 30
        os.utime(archive, (old, old))
        monitor.poll()
        self.wait()
        self.assertTrue(button.isEnabled())
        self.settings.setValue('import/enabled', False)
        self.window.cover_preferences_saved()
        self.assertFalse(button.isEnabled())
        self.settings.setValue('import/enabled', True)
        self.window.cover_preferences_saved()
        monitor.poll()
        self.wait()
        self.assertTrue(button.isEnabled())
        self.window._busy(True)
        self.assertFalse(button.isEnabled())
        self.window._busy(False)
        self.assertTrue(button.isEnabled())
        button.click()
        self.assertFalse(button.isEnabled())
        self.wait()
        self.assertEqual(self.window.iso_table.rowCount(), 1)
        self.assertTrue(archive.exists())
        self.assertFalse(button.isEnabled())

    def test_search_imports_button_disables_after_folder_becomes_empty(self):
        archive = self.archive()
        monitor = self.window.import_monitor
        monitor.poll()
        self.wait()
        self.assertTrue(self.window.import_search_button.isEnabled())
        archive.unlink()
        monitor.poll()
        self.wait()
        self.assertFalse(self.window.import_search_button.isEnabled())
        self.window._busy(True)
        self.window._busy(False)
        self.assertFalse(self.window.import_search_button.isEnabled())

    def test_busy_window_defers_import(self):
        self.archive()
        self.window.worker = object()
        self.window.import_monitor.poll()
        self.window.worker = None
        self.assertIsNone(self.window.import_monitor.worker)

    def test_recent_file_waits_and_changed_file_needs_another_scan(self):
        archive = self.archive()
        for _ in range(2):
            self.window.import_monitor.poll()
            self.wait()
        self.assertEqual(self.window.iso_table.rowCount(), 0)
        old = time.time() - 30
        os.utime(archive, (old, old))
        self.window.import_monitor.poll()
        self.wait()
        self.assertEqual(self.window.iso_table.rowCount(), 0)
        self.window.import_monitor.poll()
        self.wait()
        self.assertEqual(self.window.iso_table.rowCount(), 1)

    def test_failed_import_is_not_retried_each_poll(self):
        archive = self.folder / 'broken.zip'
        archive.write_bytes(b'PK\x03\x04broken')
        old = time.time() - 30
        os.utime(archive, (old, old))
        with patch('opldeck.ui.gui.QMessageBox.warning'), patch.object(self.window, 'start_folder_archive', wraps=self.window.start_folder_archive) as start:
            for _ in range(4):
                self.window.import_monitor.poll()
                self.wait()
        self.assertEqual(start.call_count, 1)
        self.assertTrue(archive.exists())

    def test_failed_transfer_preserves_imported_files_and_zip(self):
        archive = self.detected()
        jobs = self.jobs()
        run_batch(jobs)
        with patch('opldeck.ui.gui.QMessageBox.warning'):
            self.window.start_batch(jobs)
            self.wait()
        self.assertTrue(archive.exists() and jobs[0].source.exists())
        self.assertEqual(self.window.iso_table.rowCount(), 1)

    def test_stale_scan_after_settings_change_cannot_import(self):
        archive = self.archive()
        monitor = self.window.import_monitor
        old_generation = monitor.generation
        result = discover(self.folder)
        self.settings.setValue('import/enabled', False)
        monitor.reset()
        monitor.scanned(old_generation, result, '')
        self.assertEqual(self.window.iso_table.rowCount(), 0)
        self.assertFalse((self.root / 'cache').exists())

    def test_settings_selection_cancel_and_apply_persist(self):
        manager = ThemeManager(self.app, self.settings)
        self.addCleanup(manager.deleteLater)
        self.addCleanup(lambda: self.app.removeEventFilter(manager))
        dialog = SettingsDialog(manager=manager, initial_tab='import')
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.tabs.currentIndex(), 2)
        self.assertEqual(dialog.import_cleanup.currentData(), 'always')
        dialog.import_cleanup.setCurrentIndex(dialog.import_cleanup.findData('never'))
        dialog.reject()
        self.assertEqual(ImportPreferences.load(self.settings).cleanup, 'always')
        dialog = SettingsDialog(manager=manager, initial_tab='import')
        self.addCleanup(dialog.deleteLater)
        with patch('opldeck.ui.settings_gui.QFileDialog.getExistingDirectory', return_value=str(self.folder)):
            dialog.choose_import_folder()
        dialog.import_cleanup.setCurrentIndex(dialog.import_cleanup.findData('never'))
        self.assertTrue(dialog.save())
        self.assertEqual(ImportPreferences.load(self.settings).cleanup, 'never')

    def test_unknown_folder_rejected_without_saving(self):
        manager = ThemeManager(self.app, self.settings)
        self.addCleanup(manager.deleteLater)
        self.addCleanup(lambda: self.app.removeEventFilter(manager))
        dialog = SettingsDialog(manager=manager)
        self.addCleanup(dialog.deleteLater)
        dialog.import_folder = str(self.root / 'missing')
        self.assertFalse(dialog.save())
        self.assertEqual(ImportPreferences.load(self.settings).folder, str(self.folder))
