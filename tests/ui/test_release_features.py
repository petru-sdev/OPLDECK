import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
from threading import Event
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from opldeck.covers.artwork import encode_image, load_cover
from opldeck.core.batch import BatchProgress, BatchResult, TransferJob, run_batch
from opldeck.ui.batch_gui import BatchDialog
from opldeck.core.conversion import Progress
from opldeck.covers.cover_batch import CoverSaveJob, save_covers
from opldeck.ui.cover_batch_gui import CoverBatchDialog, SaveCoversWorker
from opldeck.covers.cover_network import CoverFetcher
from opldeck.covers.cover_preload import CoverKey, CoverPreloader
from opldeck.library.duplicates import GameOccurrence, duplicate_games
from opldeck.core.duration import duration_text
from opldeck.library.devices import Volume
from opldeck.core.formats import Media
from opldeck.ui.gui import MainWindow, iso_rows
from opldeck.ui.queue_gui import TransferQueue
from tests.fixtures import make_iso
from tests.covers.test_cover_network import Manager


class DurationTests(unittest.TestCase):
    def test_unit_boundaries_and_nonfinite_estimates(self):
        for seconds, expected in ((0, '0 s'), (59.99, '59 s'), (60, '1 min'), (61, '1 min 1 s'),
                                  (3599, '59 min 59 s'), (3600, '1 h'), (3661, '1 h 1 min 1 s'),
                                  (86400, '1 d'), (90061, '1 d 1 h 1 min 1 s'), (-1, '0 s'),
                                  (None, 'aguardando estimativa'), (float('inf'), 'aguardando estimativa')):
            with self.subTest(seconds=seconds):
                self.assertEqual(duration_text(seconds), expected)

    def test_duplicate_ids_across_formats_and_ul_records(self):
        iso = GameOccurrence('SLUS_123.45', 'One', 'ISO', Path('/library/one.iso'))
        ul = GameOccurrence('SLUS_123.45', 'Other title', 'UL', Path('/usb/ul.cfg'))
        repeated = GameOccurrence('SLUS_123.45', 'Repeated entry', 'UL', Path('/usb/ul.cfg'), 1)
        invalid = GameOccurrence('—', 'Broken', 'ISO', Path('/library/broken.iso'))
        other = GameOccurrence('SLUS_123.46', 'Other region', 'ISO', Path('/library/two.iso'))
        self.assertEqual(duplicate_games([iso, iso]), {})
        self.assertEqual(duplicate_games([iso, ul, repeated, iso, invalid, other]),
                         {'SLUS_123.45': (iso, ul, repeated)})


class FeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library = self.root / 'usb'
        self.library.mkdir()
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.image = QImage(60, 90, QImage.Format.Format_RGB32)
        self.image.fill(0xff123456)
        self.data = encode_image(self.image, '.png')
        self.keys = [CoverKey.make(self.library, f'SLUS_100.{i:02}', f'Game {i}') for i in range(3)]

    def wait(self, predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.002)
        self.fail('Operação não concluiu no prazo')

    def window(self):
        window = MainWindow(auto_devices=False, cover_preloader=CoverPreloader(settings=self.settings))
        window.import_monitor.timer.stop()
        window.storage_timer.stop()
        def close():
            window.pool.waitForDone(5000)
            self.app.processEvents()
            window.close()
            window.deleteLater()
        self.addCleanup(close)
        return window

    def jobs(self):
        return tuple(TransferJob('install', make_iso(self.root / f'Game {i}.iso', key.game_id),
                                 self.library, key.game_id, key.title, 49152, Media.DVD)
                     for i, key in enumerate(self.keys))

    def cover_jobs(self):
        jobs = []
        for index, key in enumerate(self.keys):
            preview = self.root / f'preview-{index}.png'
            preview.write_bytes(self.data)
            loaded = load_cover(key.root, key.game_id)
            jobs.append(CoverSaveJob(key, preview, loaded.state, loaded.path is not None))
        return jobs

    def local_cover(self, key, *, legacy=False):
        art = key.root / 'ART'
        art.mkdir(exist_ok=True)
        path = art / (f'{key.title}_COV.jpg' if legacy else f'{key.game_id}_COV.png')
        path.write_bytes(encode_image(self.image, path.suffix))
        return path

    def dialog(self, *, online=False, cache_entries=256):
        self.settings.setValue('providers/enabled', online)
        manager = Manager()
        service = CoverPreloader(settings=self.settings, fetcher=CoverFetcher(manager=manager),
                                 max_entries=cache_entries)
        dialog = CoverBatchDialog(self.keys, settings=self.settings, preloader=service)
        def close():
            if dialog.worker:
                dialog.worker.cancel.set()
                dialog.pool.waitForDone(5000)
                self.app.processEvents()
            dialog.reject()
            service.pool.waitForDone(5000)
            self.app.processEvents()
            dialog.deleteLater()
        self.addCleanup(close)
        return dialog, service, manager

    def test_queue_does_not_finish_at_100_percent_before_commit_and_keeps_done_on_retry(self):
        jobs = self.jobs()
        queue = TransferQueue()
        self.addCleanup(queue.deleteLater)
        queue.begin(jobs)
        queue.progress(BatchProgress(1, 3, 'Game 0', 'ISO → UL', Progress(100, 100, 1, 1), 100, 300, 'sync'))
        self.assertEqual(queue.states[0], 'Sincronizando')
        self.assertEqual(queue.table.item(0, 1).text(), '99%')
        queue.finish(BatchResult(jobs, ('done',), 2, 'Falhou no segundo'))
        self.assertEqual(queue.states, ['Concluído', 'Falhou', 'Pendente'])
        self.assertEqual(queue.pending_jobs, jobs[1:])
        queue.begin(jobs[1:], retry=True)
        queue.finish(BatchResult(jobs[1:], ('second', 'third'), 2))
        self.assertEqual(queue.states, ['Concluído'] * 3)
        self.assertFalse(queue.retry.isEnabled())

    def test_cover_confirmation_cannot_save_invalidated_or_closed_preview(self):
        self.local_cover(self.keys[0])
        for action in ('invalidate', 'reject'):
            with self.subTest(action=action):
                dialog, _, _ = self.dialog()
                self.wait(lambda: not dialog.preparing)
                dialog.preserve.setChecked(False)
                self.assertTrue(dialog.save.isEnabled())
                def confirm():
                    getattr(dialog, action)()
                    return QMessageBox.StandardButton.Yes
                with patch.object(QMessageBox, 'exec', side_effect=confirm), \
                     patch.object(dialog.pool, 'start') as start:
                    dialog.start_save()
                    try:
                        start.assert_not_called()
                        self.assertIsNone(dialog.worker)
                    finally:
                        dialog.worker = None

    def test_cover_selection_keeps_unchecked_rows_and_preserves_existing_covers(self):
        for key in self.keys:
            self.local_cover(key)
        dialog, _, _ = self.dialog()
        self.wait(lambda: not dialog.preparing)
        self.assertFalse(dialog.save.isEnabled())
        dialog.preserve.setChecked(False)
        self.assertEqual(dialog.selected_rows(), [0, 1, 2])
        dialog.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
        dialog.update_selection(rows=(2,))
        self.assertEqual(dialog.selected_rows(), [0, 2])
        dialog.preserve.setChecked(True)
        self.assertEqual(dialog.selected_rows(), [])
        self.assertFalse(dialog.save.isEnabled())
        self.assertEqual([dialog.table.item(i, 3).text() for i in range(3)], ['Preservada'] * 3)

    def test_unexpected_cover_failure_keeps_completed_results_and_pending_rows(self):
        from opldeck.covers.artwork import save_cover
        jobs = self.cover_jobs()
        worker = SaveCoversWorker(jobs, False, None)
        progress, results = [], []
        worker.signals.progress.connect(progress.append)
        worker.signals.finished.connect(results.append)
        def write(root, game_id, image, state):
            if game_id == self.keys[1].game_id:
                raise RuntimeError('Falha inesperada simulada')
            return save_cover(root, game_id, image, state)
        with patch('opldeck.covers.cover_batch.save_cover', side_effect=write):
            worker.run()
        self.assertTrue(jobs[0].state.path.is_file())
        self.assertEqual([(i, status) for i, status, _ in results[0].outcomes],
                         [(0, 'Salva'), (1, 'Falhou')])
        self.assertEqual(results[0].outcomes[0], progress[0])
        self.assertFalse(jobs[2].state.path.exists())

    def test_device_scan_waits_for_modal_confirmation_and_resumes(self):
        window = self.window()
        dialog = QDialog(window)
        self.addCleanup(dialog.deleteLater)
        dialog.setModal(True)
        dialog.show()
        self.app.processEvents()
        window.active_volume = Volume(self.library, '/dev/test', 'Test', '1')
        window.pending_volume = True
        try:
            with patch.object(window.pool, 'start') as start:
                window.maybe_scan_device()
                self.assertIsNone(window.worker)
                self.assertTrue(window.pending_volume)
                dialog.accept()
                self.wait(lambda: window.worker is not None)
                self.assertTrue(window.device_scanning)
                start.assert_called_once()
        finally:
            dialog.reject()
            window.worker = None
            window.pending_volume = False

    def test_queue_only_begins_after_cleanup_confirmation(self):
        window = self.window()
        jobs = self.jobs()
        observed = []
        def confirm(_):
            observed.append(window.transfer_queue.running)
            return False
        try:
            with patch.object(window.import_monitor, 'for_jobs', return_value=[{}]), \
                 patch.object(window, 'import_cleanup_choice', side_effect=confirm), \
                 patch.object(window.pool, 'start') as start:
                window.start_batch(jobs)
                self.assertEqual(observed, [False])
                self.assertTrue(window.transfer_queue.running)
                start.assert_called_once()
        finally:
            window.worker = None

    def test_real_batch_retry_after_cancellation_preserves_first_output(self):
        window = self.window()
        jobs = self.jobs()
        # Cancel inside the worker callback so the next game cannot race it.
        def first_run(jobs, *, cancel, progress):
            def report(event):
                progress(event)
                if event.index == 1 and event.phase == 'complete':
                    cancel.set()
            return run_batch(jobs, cancel=cancel, progress=report)
        with patch('opldeck.ui.gui.run_batch', side_effect=first_run):
            window.start_batch(jobs)
            self.wait(lambda: window.worker is None)
        self.assertEqual(window.transfer_queue.states, ['Concluído', 'Pendente', 'Pendente'])
        before = {p.name: p.read_bytes() for p in self.library.glob('ul.*.00')}
        window.retry_batch()
        self.wait(lambda: window.worker is None)
        self.assertEqual(window.transfer_queue.states, ['Concluído'] * 3)
        for name, data in before.items():
            self.assertEqual((self.library / name).read_bytes(), data)

    def test_batch_order_keeps_title_and_manual_media_together(self):
        jobs = self.jobs()
        dialog = BatchDialog(iso_rows([str(job.source) for job in jobs]), 'install')
        self.addCleanup(dialog.deleteLater)
        dialog.destination.setText(str(self.library))
        for media in dialog.media:
            media.setCurrentText('DVD')
        dialog.media[0].setCurrentText('CD')
        dialog.titles[0].setText('Edited')
        dialog.auto_media.setChecked(True)
        dialog.table.selectRow(0)
        dialog.move_selected(1)
        dialog.auto_media.setChecked(False)
        reordered = dialog.jobs()
        self.assertEqual(reordered[1].title, 'Edited')
        self.assertEqual(reordered[1].media, Media.CD)
        self.assertEqual(reordered[1].source, jobs[0].source)

    def test_duplicates_include_hidden_iso_and_ul_without_changing_status(self):
        window = self.window()
        jobs = self.jobs()
        rows = iso_rows([str(jobs[0].source)])
        window.append_rows(window.iso_table, rows)
        ul_row = list(rows[0])
        ul_row[0], ul_row[6] = 'Partes presentes', str(self.library / 'ul.cfg')
        window.append_rows(window.ul_table, [ul_row])
        window.iso_table.setRowHidden(0, True)
        groups = window.duplicate_groups()
        self.assertEqual(len(groups[self.keys[0].game_id]), 2)
        self.assertEqual(window.ul_table.item(0, 0).text(), 'Partes presentes')

    def test_batch_cover_save_preserves_existing_and_continues_after_stale_preview(self):
        existing = self.local_cover(self.keys[0])
        before = existing.read_bytes()
        jobs = self.cover_jobs()
        changed = self.local_cover(self.keys[1])  # Created after the preview.
        result = save_covers(jobs)
        self.assertEqual([status for _, status, _ in result.outcomes], ['Preservada', 'Preservada', 'Salva'])
        self.assertEqual(existing.read_bytes(), before)
        self.assertEqual(changed.read_bytes(), self.data)
        self.assertTrue(jobs[2].state.path.exists())

    def test_cover_changed_after_preview_is_never_overwritten(self):
        jobs = self.cover_jobs()
        changed = self.local_cover(self.keys[0])
        result = save_covers(jobs, preserve=False)
        self.assertEqual([status for _, status, _ in result.outcomes], ['Falhou', 'Salva', 'Salva'])
        self.assertEqual(changed.read_bytes(), self.data)

    def test_retry_preflight_rejects_existing_output_without_touching_it(self):
        jobs = self.jobs()
        first = run_batch(jobs[:1])
        self.assertFalse(first.error)
        before = {p.name: p.read_bytes() for p in self.library.iterdir()}
        blocked = run_batch(jobs)
        self.assertTrue(blocked.error)
        self.assertEqual(blocked.attempted, 0)
        self.assertEqual({p.name: p.read_bytes() for p in self.library.iterdir()}, before)

    def test_single_transfer_is_visible_and_retry_handles_missing_source(self):
        window = self.window()
        source = self.root / 'missing.iso'
        with patch.object(QMessageBox, 'warning'):
            window.start_install(source, self.library, 'Missing', Media.DVD)
            self.wait(lambda: window.worker is None)
            self.assertEqual(window.transfer_queue.states, ['Falhou'])
            make_iso(source)
            window.retry_batch()
            self.wait(lambda: window.worker is None)
        self.assertEqual(window.transfer_queue.states, ['Concluído'])
        self.assertEqual(window.transfer_queue.table.item(0, 2).text(), 'SLUS_123.45')

    def test_cancel_preparation_never_writes_and_releases_temporary_previews(self):
        dialog, service, manager = self.dialog(online=True)
        self.wait(lambda: service.fetcher.busy)
        spool = Path(dialog.spool.name)
        dialog.stop()
        self.assertFalse(dialog.preparing)
        self.assertFalse((self.library / 'ART').exists())
        self.assertTrue(manager.reply.aborted)
        dialog.reject()
        self.assertFalse(spool.exists())

    def test_cover_save_cancel_keeps_completed_and_does_not_create_next(self):
        jobs = self.cover_jobs()
        cancel = Event()
        result = save_covers(jobs, cancel=cancel, progress=lambda _: cancel.set())
        self.assertTrue(result.cancelled)
        self.assertEqual(len(result.outcomes), 1)
        self.assertTrue(jobs[0].state.path.exists())
        self.assertFalse(jobs[1].state.path.exists())

    def test_volume_guard_prevents_cover_writes(self):
        from types import SimpleNamespace
        jobs = self.cover_jobs()
        with patch('opldeck.covers.cover_batch.require_volume', side_effect=ValueError('Pendrive removido')):
            result = save_covers(jobs, volume=SimpleNamespace(root=self.library))
        self.assertEqual([status for _, status, _ in result.outcomes], ['Falhou'] * 3)
        self.assertFalse((self.library / 'ART').exists())

    def test_cover_dialog_bounded_cache_spools_all_previews_and_keeps_legacy_sources(self):
        originals = [self.local_cover(key, legacy=True) for key in self.keys]
        before = [path.read_bytes() for path in originals]
        dialog, service, _ = self.dialog(cache_entries=1)
        self.wait(lambda: not dialog.preparing)
        self.assertEqual(len(dialog.jobs), 3)
        self.assertLessEqual(len(service.cache), 1)
        self.assertFalse(dialog.save.isEnabled())
        dialog.preserve.setChecked(False)
        self.assertTrue(dialog.save.isEnabled())
        dialog.start_save()
        self.wait(lambda: dialog.worker is None)
        self.assertEqual(len(dialog.saved_paths), 3)
        for path, data in zip(originals, before):
            self.assertEqual(path.read_bytes(), data)
        for key in self.keys:
            self.assertTrue((self.library / 'ART' / f'{key.game_id}_COV.png').exists())

    def test_online_previews_only_write_after_explicit_save_and_respect_checks(self):
        dialog, service, manager = self.dialog(online=True)
        for _ in self.keys:
            self.wait(lambda: service.fetcher.busy)
            reply = manager.reply
            reply.headers(length=len(self.data))
            reply.feed(self.data)
            reply.finish()
        self.wait(lambda: not dialog.preparing)
        self.assertFalse((self.library / 'ART').exists())
        dialog.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
        dialog.start_save()
        self.wait(lambda: dialog.worker is None)
        self.assertEqual(len(dialog.saved_paths), 2)
        self.assertFalse((self.library / 'ART' / f'{self.keys[1].game_id}_COV.png').exists())

    def test_cover_dialog_replacement_decline_and_invalidation_block_saving(self):
        for key in self.keys:
            self.local_cover(key)
        dialog, _, _ = self.dialog()
        self.wait(lambda: not dialog.preparing)
        dialog.preserve.setChecked(False)
        with patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.No):
            dialog.start_save()
        self.assertIsNone(dialog.worker)
        self.assertEqual(dialog.saved_paths, [])
        dialog.invalidate()
        self.assertFalse(dialog.save.isEnabled())

    def test_duration_is_applied_to_activity_and_eta(self):
        window = self.window()
        window.activity.started = time.monotonic() - 3661
        window.activity.animate()
        self.assertIn('1 h 1 min 1 s', window.activity.elapsed.text())
        window.started = time.monotonic() - 61
        window.operation = 'ISO → UL'
        window.last_progress = Progress(1, 100, 1, 1)
        with patch.object(window.transfer_rate, 'estimate', return_value=(1, 3600, False)):
            window.update_transfer()
        self.assertIn('decorrido 1 min 1 s', window.transfer_details.text())
        self.assertIn('restante estimado 1 h', window.transfer_details.text())

    def test_copy_log_and_transfer_history(self):
        from opldeck import __version__
        from opldeck.ui.history_gui import TransferHistoryDialog
        self.assertEqual(__version__, '0.8.0')
        window = self.window()
        menu_titles = [action.text() for action in window.menuBar().actions()]
        self.assertEqual(menu_titles, ['Biblioteca', 'Configurações', 'Histórico'])
        self.assertEqual(window.copy_log_button.text(), 'Copiar Log')
        window.log('INFO', 'Linha de teste para cópia')
        window.copy_log_button.click()
        self.assertIn('Linha de teste para cópia', self.app.clipboard().text())
        jobs = self.jobs()
        window.start_batch(jobs[:2])
        self.wait(lambda: window.worker is None)
        window.start_install(jobs[2].source, self.library, jobs[2].title, jobs[2].media)
        self.wait(lambda: window.worker is None)
        self.assertEqual(len(window.transfer_history), 3)
        self.assertEqual([e.game_id for e in window.transfer_history], [k.game_id for k in self.keys])
        reloaded = self.window()
        self.assertEqual(len(reloaded.transfer_history), 3)
        dialog = TransferHistoryDialog(reloaded.transfer_history, on_clear=reloaded.clear_transfer_history, parent=reloaded)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.table.rowCount(), 3)
        dialog.clear_button.click()
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertEqual(reloaded.transfer_history, [])
        self.assertEqual(self.window().transfer_history, [])
