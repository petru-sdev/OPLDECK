import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox
    from opldeck.ui.batch_gui import BatchDialog
    from opldeck.ui.gui import MainWindow, iso_rows
except ModuleNotFoundError:
    QApplication = None

from opldeck.core.batch import BatchProgress, BatchResult, TransferJob
from opldeck.core.conversion import Progress, install_iso
from opldeck.core.formats import Media
from opldeck.core.ul import read_cfg
from tests.fixtures import make_iso


@unittest.skipIf(QApplication is None, "PySide6 não instalado")
class BatchGUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.destination = self.root / "usb"
        self.destination.mkdir()
        self.paths = [make_iso(self.root / f"Game {i}.iso", f"SLUS_100.{i:02}") for i in range(5)]
        self.window = MainWindow(auto_devices=False)
        self.window.show()
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        self.window.pool.waitForDone(5000)
        self.app.processEvents()
        self.window.close()

    def wait_scan(self):
        deadline = time.monotonic() + 5
        while self.window.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertIsNone(self.window.worker)

    def select_all(self):
        self.window.load_isos(list(map(str, self.paths)))
        self.wait_scan()
        self.window.iso_table.selectAll()

    def test_preview_requires_destination_media_and_unique_titles(self):
        dialog = BatchDialog(iso_rows(list(map(str, self.paths))), "install")
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.start_button.isEnabled())
        dialog.destination.setText(str(self.destination))
        self.assertFalse(dialog.start_button.isEnabled())
        for combo in dialog.media:
            combo.setCurrentText("DVD")
        self.assertTrue(dialog.start_button.isEnabled())
        dialog.titles[1].setText(dialog.titles[0].text())
        self.assertFalse(dialog.start_button.isEnabled())
        dialog.titles[1].setText("Second")
        self.assertEqual(len(dialog.jobs()), 5)
        dialog.reject()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_five_selected_install_with_two_bars_and_automatic_refresh(self):
        self.select_all()
        observed = []
        real = self.window._batch_progress

        def observe(event):
            real(event)
            observed.append((event.index, event.phase, self.window.progress_bar.value(),
                             self.window.overall_progress.value()))

        def approve(dialog):
            dialog.destination.setText(str(self.destination))
            for combo in dialog.media:
                combo.setCurrentText("DVD")
            dialog.accept()
            return dialog.result()

        with patch.object(BatchDialog, "exec", approve), patch.object(self.window, "_batch_progress", observe):
            self.window.choose_install()
            self.assertTrue(self.window.overall_progress.isVisible())
            self.wait_scan()
        self.assertEqual(len(read_cfg(self.destination / "ul.cfg")), 5)
        self.assertEqual(self.window.ul_table.rowCount(), 5)
        self.assertEqual(self.window.overall_progress.value(), 1000)
        self.assertEqual(self.window.progress_bar.value(), 1000)
        self.assertIn((2, "preparing", 0, 200), observed)
        self.assertIn("5 de 5 jogos concluídos", self.window.batch_label.text())
        self.assertFalse(self.window.cancel_button.isEnabled())

    def test_context_preserves_multiple_selection_and_single_actions_disabled(self):
        self.select_all()
        table = self.window.iso_table
        actions = self.window.table_actions[table]
        with patch.object(QMenu, "popup"):
            self.window.show_context_menu(table, table.visualItemRect(table.item(0, 2)).center())
        self.assertEqual(len(self.window._selected_values(table)), 5)
        self.assertTrue(actions["convert"].isEnabled())
        self.assertFalse(actions["details"].isEnabled())
        self.assertFalse(actions["folder"].isEnabled())
        table.selectionModel().select(table.model().index(4, 0),
            QItemSelectionModel.SelectionFlag.Deselect | QItemSelectionModel.SelectionFlag.Rows)
        with patch.object(QMenu, "popup"):
            self.window.show_context_menu(table, table.visualItemRect(table.item(4, 2)).center())
        self.assertEqual(len(self.window._selected_values(table)), 1)

    def test_filtered_rows_do_not_transfer_and_remove_keeps_files(self):
        self.select_all()
        table = self.window.iso_table
        # Um filtro pode esconder linhas selecionadas; só as visíveis integram a fila.
        table.setRowHidden(1, True)
        self.assertEqual(len(self.window._selected_values(table)), 4)
        self.window.remove_selected(table)
        self.assertEqual(table.rowCount(), 1)
        self.assertTrue(all(path.exists() for path in self.paths))

    def test_more_than_four_gib_progress_never_finishes_before_commit(self):
        self.window.batch_index = 0
        event = BatchProgress(2, 5, "Large", "ISO → UL", Progress(4 << 30, 4 << 30, 4, 4),
                              8 << 30, 20 << 30, "transfer")
        self.window._batch_progress(event)
        self.assertEqual(self.window.progress_bar.value(), 999)
        self.assertEqual(self.window.overall_progress.value(), 400)
        self.assertIn("Jogo 2 de 5", self.window.batch_label.text())

    def test_cancel_button_stops_queue_and_reports_partial_result(self):
        jobs = tuple(TransferJob("install", path, self.destination, f"SLUS_100.{i:02}", f"Game {i}",
                                  path.stat().st_size, Media.DVD) for i, path in enumerate(self.paths))

        def cancellable(jobs, *, cancel, progress):
            self.assertTrue(cancel.wait(3))
            return BatchResult(jobs, (), 0, "Conversão cancelada; arquivos parciais removidos.", True)

        with patch("opldeck.ui.gui.run_batch", side_effect=cancellable):
            self.window.start_batch(jobs)
            self.window.cancel_button.click()
            self.wait_scan()
        self.assertIn("Lote cancelado", self.window.batch_label.text())
        self.assertIn("5 não iniciados", self.window.batch_label.text())
        self.assertEqual(self.window.overall_progress.value(), 0)
        self.assertEqual(list(self.destination.iterdir()), [])

    def select_ul_games(self):
        entries = [install_iso(path, self.destination, media=Media.DVD) for path in self.paths]
        (self.destination / entries[1].part_name(0)).write_bytes(b'')
        (self.destination / entries[2].part_name(0)).unlink()
        self.window.load_cfg(self.destination / 'ul.cfg')
        self.wait_scan()
        self.window.show_table(self.window.ul_table)
        self.window.ul_table.selectAll()
        return entries

    def test_delete_toolbar_removes_many_including_damaged_with_one_confirmation(self):
        entries = self.select_ul_games()
        button = self.window.management_buttons['delete']
        self.assertTrue(button.isEnabled())
        self.assertFalse(self.window.management_buttons['rename'].isEnabled())
        confirmations = []
        def confirm(dialog):
            confirmations.append(dialog.text())
            self.assertIn('5 jogos', dialog.text())
            for entry in entries:
                self.assertIn(entry.game_id, dialog.detailedText())
            return QMessageBox.StandardButton.Yes
        with patch('opldeck.ui.gui.QMessageBox.exec', confirm):
            button.click()
            self.wait_scan()
        self.assertEqual(len(confirmations), 1)
        self.assertEqual(read_cfg(self.destination / 'ul.cfg'), [])
        self.assertEqual(self.window.ul_table.rowCount(), 0)
        self.assertIn('5 de 5 jogos excluídos', self.window.batch_label.text())
        self.assertFalse(button.isEnabled())
        self.assertTrue(all(path.exists() for path in self.paths))

    def test_context_delete_preserves_multiselection_and_decline_keeps_files(self):
        entries = self.select_ul_games()
        table = self.window.ul_table
        with patch.object(QMenu, 'popup'):
            self.window.show_context_menu(table, table.visualItemRect(table.item(2, 2)).center())
        self.assertEqual(len(self.window._selected_values(table)), 5)
        action = self.window.table_actions[table]['delete']
        self.assertTrue(action.isEnabled())
        self.assertIn('5 jogos', action.text())
        with patch('opldeck.ui.gui.QMessageBox.exec', return_value=QMessageBox.StandardButton.No) as confirm:
            action.trigger()
        confirm.assert_called_once()
        self.assertEqual(read_cfg(self.destination / 'ul.cfg'), entries)

    def test_context_delete_only_removes_visible_selected_rows(self):
        self.select_ul_games()
        table = self.window.ul_table
        kept = table.item(1, 1).text()
        table.setRowHidden(1, True)
        with patch.object(QMenu, 'popup'):
            self.window.show_context_menu(table, table.visualItemRect(table.item(0, 2)).center())
        with patch('opldeck.ui.gui.QMessageBox.exec', return_value=QMessageBox.StandardButton.Yes):
            self.window.table_actions[table]['delete'].trigger()
            self.wait_scan()
        self.assertEqual([entry.game_id for entry in read_cfg(self.destination / 'ul.cfg')], [kept])

    def test_delete_selection_pending_operation_disables_menu_and_button(self):
        self.select_ul_games()
        from opldeck.core.conversion import TRANSACTION_DIR
        (self.destination / TRANSACTION_DIR).mkdir()
        self.window.load_cfg(self.destination / 'ul.cfg')
        self.wait_scan()
        self.window.ul_table.selectAll()
        self.assertFalse(self.window.table_actions[self.window.ul_table]['delete'].isEnabled())
        self.assertFalse(self.window.management_buttons['delete'].isEnabled())

    def test_delete_failure_refreshes_partial_results_and_stops_remaining(self):
        self.select_ul_games()
        from opldeck.library.management import delete_ul
        ids = [row[1] for row in self.window._selected_values(self.window.ul_table)]
        def fail_second(cfg, game_id, **kwargs):
            if game_id == ids[1]:
                raise OSError('Falha de teste')
            return delete_ul(cfg, game_id, **kwargs)
        with patch('opldeck.ui.gui.QMessageBox.exec', return_value=QMessageBox.StandardButton.Yes), \
                patch('opldeck.ui.gui.QMessageBox.warning') as warning, \
                patch('opldeck.library.deletion.delete_ul', side_effect=fail_second):
            self.window.management_buttons['delete'].click()
            self.wait_scan()
        warning.assert_called_once()
        self.assertIn('1 de 5 jogos excluídos', self.window.batch_label.text())
        remaining = {entry.game_id for entry in read_cfg(self.destination / 'ul.cfg')}
        self.assertEqual(remaining, set(ids[1:]))
        self.assertEqual(self.window.ul_table.rowCount(), 4)

    def test_delete_cancel_button_stops_worker_and_reports_summary(self):
        self.select_ul_games()
        from opldeck.library.deletion import DeleteResult
        def cancellable(plan, *, cancel, progress):
            self.assertTrue(cancel.wait(3))
            return DeleteResult(plan, (), 'Exclusão cancelada', True)
        with patch('opldeck.ui.gui.QMessageBox.exec', return_value=QMessageBox.StandardButton.Yes), \
                patch('opldeck.ui.gui.delete_selection', side_effect=cancellable):
            self.window.management_buttons['delete'].click()
            self.assertFalse(self.window.management_buttons['delete'].isEnabled())
            self.window.cancel_button.click()
            self.wait_scan()
        self.assertIn('cancelada', self.window.batch_label.text())
        self.assertEqual(len(read_cfg(self.destination / 'ul.cfg')), 5)
