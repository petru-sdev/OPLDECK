import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox
    from opldeck.ui.gui import MainWindow, ul_rows
    from opldeck.ui.recovery_gui import RebuildDialog
except ModuleNotFoundError:
    QApplication = None

from opldeck.core.formats import Media
from opldeck.core.conversion import CancelledError, Progress, TRANSACTION_DIR, install_iso
from opldeck.core.ul import ULEntry
from opldeck.library.recovery import scan_ul
from tests.fixtures import make_bin, make_iso


@unittest.skipIf(QApplication is None, "PySide6 não instalado")
class GUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
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
        self.assertIsNone(self.window.worker, "Leitura assíncrona não terminou")

    def test_iso_import_errors_dedup_and_clear(self):
        path = make_iso(self.root / "Teste.iso")
        self.window.load_isos([str(path), str(self.root / "missing.iso")])
        self.wait_scan()
        self.assertEqual(self.window.iso_table.rowCount(), 2)
        ids = [self.window.iso_table.item(row, 1).text() for row in range(2)]
        self.assertIn("SLUS_123.45", ids)
        self.assertIn("—", ids)
        self.window.load_isos([str(path)])
        self.assertEqual(self.window.iso_table.rowCount(), 2)
        self.window.clear_table(self.window.iso_table)
        self.assertEqual(self.window.iso_table.rowCount(), 0)
        self.assertFalse(self.window.imported)

    def test_cfg_missing_and_existing_parts(self):
        entry = ULEntry("Synthetic", "SLUS_123.45", 1, Media.DVD)
        cfg = self.root / "ul.cfg"
        cfg.write_bytes(entry.to_bytes())
        self.window.load_cfg(cfg)
        self.wait_scan()
        self.assertIn("Partes ausentes", self.window.ul_table.item(0, 0).text())
        part = self.root / entry.part_name(0)
        part.write_bytes(b"test")
        self.window.load_cfg(cfg)
        self.wait_scan()
        self.assertEqual(self.window.ul_table.rowCount(), 1)
        self.assertEqual(self.window.ul_table.item(0, 0).text(), "Partes presentes")
        part.write_bytes(b"")
        self.assertEqual(ul_rows(str(cfg))[0][0], "Tamanho de parte inválido: 00 (0 bytes)")

    def test_conversion_roundtrip_and_technical_log(self):
        path = make_iso(self.root / "Teste.iso")
        destination = self.root / "usb"
        destination.mkdir()
        self.window.start_install(path, destination, "Synthetic", Media.DVD)
        self.wait_scan()
        self.assertEqual(self.window.ul_table.rowCount(), 1)
        self.assertEqual(self.window.ul_table.columnCount(), 11)
        self.assertEqual(self.window.ul_table.item(0, 7).text(), str(path.stat().st_size))
        self.window.ul_table.selectRow(0)
        self.assertIn("CRC USBExtreme:", self.window.details.toPlainText())
        output = self.root / "out.iso"
        self.window.start_export(destination / "ul.cfg", "SLUS_123.45", output)
        self.wait_scan()
        self.assertEqual(output.read_bytes(), path.read_bytes())
        self.assertEqual(self.window.progress_bar.value(), 1000)
        log = self.window.log_view.toPlainText()
        self.assertIn("[INFO]", log)
        self.assertIn("Índice publicado", log)
        self.assertIn("UL → ISO concluída", log)
        self.assertIn("MiB/s", self.window.transfer_details.text())

    def test_conversion_failure_is_logged(self):
        destination = self.root / "usb"
        destination.mkdir()
        with patch("opldeck.ui.gui.QMessageBox.warning") as warning:
            self.window.start_install(self.root / "missing.iso", destination, "Missing", Media.CD)
            self.wait_scan()
            warning.assert_called_once()
        self.assertIn("[ERROR]", self.window.log_view.toPlainText())
        self.assertFalse(self.window.cancel_button.isEnabled())

    def test_ul_rename_delete_confirmation_and_refresh(self):
        iso = make_iso(self.root / "Synthetic.iso")
        entry = install_iso(iso, self.root, media=Media.DVD)
        cfg = self.root / "ul.cfg"
        self.window.load_cfg(cfg)
        self.wait_scan()
        self.window.ul_table.selectRow(0)
        with patch("opldeck.ui.gui.QInputDialog.getText", return_value=("Renamed", True)):
            self.window.choose_rename()
            self.wait_scan()
        self.assertEqual(self.window.ul_table.item(0, 2).text(), "Renamed")
        self.assertIn("bytes referenciados", self.window.transfer_details.text())
        self.window.ul_table.selectRow(0)
        with patch("opldeck.ui.gui.QMessageBox.exec", return_value=QMessageBox.StandardButton.No):
            self.window.choose_delete()
        self.assertEqual(self.window.ul_table.rowCount(), 1)
        self.assertIn(b"Renamed", cfg.read_bytes())
        confirmed = []
        def confirm(dialog):
            confirmed.append(dialog.text())
            return QMessageBox.StandardButton.Yes
        with patch("opldeck.ui.gui.QMessageBox.exec", confirm):
            self.window.choose_delete()
            self.wait_scan()
        self.assertIn(entry.game_id, confirmed[0])
        self.assertEqual(self.window.ul_table.rowCount(), 0)
        self.assertEqual(cfg.read_bytes(), b"")
        self.assertIn("Exclusão UL concluída", self.window.log_view.toPlainText())
        self.assertTrue(iso.exists())

    def test_ul_invalid_rename_logs_and_keeps_table(self):
        iso = make_iso(self.root / "Synthetic.iso")
        install_iso(iso, self.root, media=Media.DVD)
        with patch("opldeck.ui.gui.QMessageBox.warning") as warning:
            self.window.start_rename(self.root / "ul.cfg", "SLUS_123.45", "Ação")
            self.wait_scan()
        warning.assert_called_once()
        self.assertEqual(self.window.ul_table.item(0, 2).text(), "Synthetic")
        self.assertIn("[ERROR]", self.window.log_view.toPlainText())

    def test_ul_pending_status_and_recovery_control(self):
        iso = make_iso(self.root / "Synthetic.iso")
        install_iso(iso, self.root, media=Media.DVD)
        pending = self.root / TRANSACTION_DIR
        pending.mkdir()
        self.window.load_cfg(self.root / "ul.cfg")
        self.wait_scan()
        self.assertIn("pendente", self.window.ul_table.item(0, 0).text())
        self.window.ul_table.selectRow(0)
        self.assertFalse(self.window.table_actions[self.window.ul_table]['rename'].isEnabled())
        self.assertFalse(self.window.table_actions[self.window.ul_table]['delete'].isEnabled())
        with patch("opldeck.ui.gui.QFileDialog.getExistingDirectory", return_value=str(self.root)):
            self.window.choose_recovery()
            self.assertFalse(self.window.cancel_button.isEnabled())
            self.wait_scan()
        self.assertFalse(pending.exists())
        self.assertEqual(self.window.ul_table.item(0, 0).text(), "Partes presentes")
        self.assertIn("Limpeza", self.window.transfer_details.text())

    def test_damaged_ul_can_rename_and_delete_but_not_export(self):
        iso = make_iso(self.root / 'Synthetic.iso')
        entry = install_iso(iso, self.root, media=Media.DVD)
        (self.root / entry.part_name(0)).write_bytes(b'')
        self.window.load_cfg(self.root / 'ul.cfg')
        self.wait_scan()
        table = self.window.ul_table
        table.selectRow(0)
        actions = self.window.table_actions[table]
        self.assertTrue(actions['rename'].isEnabled())
        self.assertTrue(actions['delete'].isEnabled())
        self.assertFalse(actions['convert'].isEnabled())
        with patch('opldeck.ui.gui.QInputDialog.getText', return_value=('Renamed broken', True)):
            actions['rename'].trigger()
            self.wait_scan()
        self.assertEqual(table.item(0, 2).text(), 'Renamed broken')
        self.assertEqual(table.item(0, 0).text(), 'Tamanho de parte inválido: 00 (0 bytes)')

    def test_delete_incomplete_ul_confirms_actual_files_and_missing_parts(self):
        entry = ULEntry('Broken', 'SLUS_123.45', 3, Media.DVD)
        cfg = self.root / 'ul.cfg'
        cfg.write_bytes(entry.to_bytes())
        (self.root / entry.part_name(0)).write_bytes(b'truncated')
        (self.root / entry.part_name(2)).touch()
        extra = self.root / 'unrelated.txt'
        extra.write_text('keep')
        self.window.load_cfg(cfg)
        self.wait_scan()
        self.window.ul_table.selectRow(0)
        def decline(dialog):
            self.assertIn('2 de 3', dialog.informativeText())
            self.assertIn('1 parte(s) já ausentes', dialog.informativeText())
            self.assertIn(entry.part_name(0), dialog.detailedText())
            self.assertIn(entry.part_name(1), dialog.detailedText())
            self.assertEqual(dialog.defaultButton(), dialog.button(QMessageBox.StandardButton.No))
            return QMessageBox.StandardButton.No
        with patch('opldeck.ui.gui.QMessageBox.exec', decline):
            self.window.choose_delete()
        self.assertTrue((self.root / entry.part_name(0)).exists())
        with patch('opldeck.ui.gui.QMessageBox.exec', return_value=QMessageBox.StandardButton.Yes):
            self.window.choose_delete()
            self.wait_scan()
        self.assertEqual(self.window.ul_table.rowCount(), 0)
        self.assertEqual(cfg.read_bytes(), b'')
        self.assertEqual(extra.read_text(), 'keep')

    def test_delete_record_without_any_parts_handles_zero_bytes(self):
        cfg = self.root / 'ul.cfg'
        cfg.write_bytes(ULEntry('Missing', 'SLUS_123.45', 2, Media.DVD).to_bytes())
        self.window.load_cfg(cfg)
        self.wait_scan()
        self.window.ul_table.selectRow(0)
        with patch('opldeck.ui.gui.QMessageBox.exec', return_value=QMessageBox.StandardButton.Yes):
            self.window.choose_delete()
            self.wait_scan()
        self.assertEqual(cfg.read_bytes(), b'')
        self.assertEqual(self.window.progress_bar.value(), 1000)
        self.assertIn('0 / 0 bytes', self.window.transfer_details.text())

    def test_damaged_game_ficha_offers_delete_with_main_confirmation(self):
        from opldeck.ui.game_gui import GameDialog
        from PySide6.QtWidgets import QPushButton
        cfg = self.root / 'ul.cfg'
        cfg.write_bytes(ULEntry('Missing', 'SLUS_123.45', 1, Media.DVD).to_bytes())
        self.window.load_cfg(cfg)
        self.wait_scan()
        self.window.ul_table.selectRow(0)
        def open_ficha(dialog):
            delete = next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Excluir jogo…')
            self.assertTrue(delete.isEnabled())
            delete.click()
            return dialog.result()
        with patch.object(GameDialog, 'exec', open_ficha), \
                patch('opldeck.ui.gui.QMessageBox.exec', return_value=QMessageBox.StandardButton.No) as confirm:
            self.window.show_details_dialog(self.window.ul_table)
        confirm.assert_called_once()
        self.assertTrue(cfg.read_bytes())

    def test_save_log(self):
        output = self.root / "opldeck.log"
        self.window.log("WARNING", "Teste de diagnóstico")
        with patch("opldeck.ui.gui.QFileDialog.getSaveFileName", return_value=(str(output), "")):
            self.window.save_log()
        self.assertIn("[WARNING] Teste de diagnóstico", output.read_text())

    def test_cancel_button_and_log(self):
        def cancellable(*args, cancel, progress, **kwargs):
            if cancel.wait(3):
                raise CancelledError("Conversão cancelada; arquivos parciais removidos.")
            raise RuntimeError("Cancelamento não chegou ao worker")

        with patch("opldeck.ui.gui.install_iso", side_effect=cancellable):
            self.window.start_install(self.root / "sample.iso", self.root, "Sample", Media.CD)
            self.window.cancel_button.click()
            self.wait_scan()
        self.assertIn("Cancelamento solicitado", self.window.log_view.toPlainText())
        self.assertIn("Conversão cancelada", self.window.log_view.toPlainText())
        self.assertFalse(self.window.cancel_button.isEnabled())
        contextual = [self.window.import_search_button, *self.window.convert_buttons.values(),
                      *self.window.management_buttons.values()]
        self.assertTrue(all(button.isEnabled() for button in self.window.buttons if button not in contextual))
        # Cancelar libera a interface, mas não autoriza conversões sem seleção.
        self.assertTrue(all(not button.isEnabled() for button in self.window.convert_buttons.values()))
        self.assertFalse(self.window.management_buttons['delete'].isEnabled())
        self.assertFalse(self.window.usb_start.isEnabled())  # Sem pendrive, o início automático continua bloqueado.
        self.assertFalse(self.window.import_search_button.isEnabled())  # Sem pasta ativa, a busca continua bloqueada.

    def test_progress_exceeding_32_bit_integer(self):
        self.window.started = time.monotonic() - 2
        self.window._progress(Progress(4 << 30, 5 << 30, 4, 5))
        self.assertEqual(self.window.progress_bar.value(), 800)
        self.assertIn("4,294,967,296", self.window.transfer_details.text())

    def test_bin_import_details_conversion_and_log(self):
        iso = make_iso(self.root / "reference.iso")
        source = make_bin(iso, self.root / "Synthetic.bin", mode=2)
        self.window.load_bins([str(source)])
        self.wait_scan()
        self.assertEqual(self.window.bin_table.rowCount(), 1)
        self.window.bin_table.selectRow(0)
        self.assertIn("Mode 2 Form 1", self.window.details.toPlainText())
        self.assertIn("Bytes ISO: 49152", self.window.details.toPlainText())
        self.window.load_bins([str(source)])
        self.assertEqual(self.window.bin_table.rowCount(), 1)
        output = self.root / "converted.iso"
        self.window.start_bin_conversion(source, output)
        self.wait_scan()
        self.assertEqual(output.read_bytes(), iso.read_bytes())
        self.assertEqual(self.window.iso_table.rowCount(), 1)
        self.assertIn("BIN → ISO concluída", self.window.log_view.toPlainText())
        self.assertIn("setores 24/24", self.window.transfer_details.text())

    def test_bin_errors_and_clear_keep_ul_index(self):
        self.window.load_bins([str(self.root / "missing.bin")])
        self.wait_scan()
        self.assertEqual(self.window.bin_table.item(0, 1).text(), "—")
        self.window.cfg_path = self.root / "ul.cfg"
        self.window.clear_table(self.window.bin_table)
        self.assertEqual(self.window.cfg_path, self.root / "ul.cfg")
        self.assertFalse(self.window.imported_bins)
        self.assertEqual(self.window.bin_table.rowCount(), 0)

    def test_bin_conversion_failure_is_logged(self):
        iso = make_iso(self.root / "reference.iso")
        source = make_bin(iso, self.root / "Synthetic.bin")
        with source.open("r+b") as stream:
            stream.seek(23 * 2352)
            stream.write(b"X")
        output = self.root / "converted.iso"
        with patch("opldeck.ui.gui.QMessageBox.warning") as warning:
            self.window.start_bin_conversion(source, output)
            self.wait_scan()
            warning.assert_called_once()
        self.assertFalse(output.exists())
        self.assertIn("Setor raw 23", self.window.log_view.toPlainText())

    def test_counters_filters_refresh_and_tab_scope(self):
        dvd = self.root / "DVD"
        dvd.mkdir()
        valid = make_iso(dvd / "Valid.iso")
        estimated = make_iso(self.root / "Estimated.iso")
        missing = self.root / "Missing.iso"
        self.window.load_isos(list(map(str, [valid, estimated, missing])))
        self.wait_scan()
        counters = self.window.summary_counts
        self.assertEqual({k: v.text() for k, v in counters.items()},
                         {"total": "3", "ok": "1", "warning": "1", "error": "1"})
        table = self.window.iso_table
        search, status_filter, visible = self.window.table_controls[table]
        search.setText("valid")
        self.assertEqual(visible.text(), "1 de 3 jogos")
        self.assertEqual(counters["total"].text(), "3")
        search.clear()
        status_filter.setCurrentIndex(3)
        self.assertEqual(visible.text(), "1 de 3 jogos")
        make_iso(missing)
        self.window.refresh_table(table)
        self.wait_scan()
        self.assertEqual(table.rowCount(), 3)
        self.assertEqual(counters["error"].text(), "0")
        self.assertEqual(counters["warning"].text(), "2")
        self.assertEqual(visible.text(), "0 de 3 jogos")
        self.window.show_table(self.window.ul_table)
        self.assertEqual(counters["total"].text(), "0")
        self.window.show_table(self.window.iso_table)
        self.assertEqual(counters["total"].text(), "3")
        self.window.clear_table(table)
        self.assertEqual(counters["total"].text(), "0")

    def test_context_menu_selects_clicked_game_and_empty_area_disables_actions(self):
        paths = [make_iso(self.root / "Zulu.iso"), make_iso(self.root / "Alpha.iso")]
        self.window.load_isos(list(map(str, paths)))
        self.wait_scan()
        table = self.window.iso_table
        table.sortItems(2, Qt.SortOrder.AscendingOrder)
        table.selectRow(1)
        with patch.object(QMenu, "popup"):
            self.window.show_context_menu(table, table.visualItemRect(table.item(0, 2)).center())
        actions = self.window.table_actions[table]
        actions["copy_path"].trigger()
        self.assertEqual(self.app.clipboard().text(), str(paths[1]))
        with patch("opldeck.ui.gui.QDesktopServices.openUrl", return_value=True) as opener:
            actions["folder"].trigger()
        self.assertEqual(opener.call_args.args[0].toLocalFile(), str(self.root))
        actions["remove"].trigger()
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 2).text(), "Zulu")
        self.assertTrue(all(path.exists() for path in paths))
        with patch.object(QMenu, "popup"):
            self.window.show_context_menu(table, QPoint(10, table.viewport().height() - 5))
        self.assertFalse(actions["remove"].isEnabled())
        self.assertFalse(actions["convert"].isEnabled())
        self.assertFalse(actions["copy_path"].isEnabled())

    def test_delete_shortcut_does_not_delete_iso_or_fire_in_search(self):
        path = make_iso(self.root / "Synthetic.iso")
        self.window.load_isos([str(path)])
        self.wait_scan()
        table = self.window.iso_table
        table.selectRow(0)
        self.window.activateWindow()
        search = self.window.table_controls[table][0]
        search.setFocus()
        search.setText("Synthetic")
        search.selectAll()
        QTest.keyClick(search, Qt.Key.Key_Delete)
        self.assertEqual(table.rowCount(), 1)
        table.setFocus()
        self.app.processEvents()
        QTest.keyClick(table, Qt.Key.Key_Delete)
        self.assertEqual(table.rowCount(), 0)
        self.assertTrue(path.exists())
        self.window.load_isos([str(path)])
        self.wait_scan()
        self.assertEqual(table.rowCount(), 1)

    def test_ul_delete_action_confirms_and_filtered_selection_is_disabled(self):
        path = make_iso(self.root / "Synthetic.iso")
        entry = install_iso(path, self.root, media=Media.DVD)
        self.window.show_table(self.window.ul_table)
        self.window.load_cfg(self.root / "ul.cfg")
        self.wait_scan()
        table = self.window.ul_table
        table.selectRow(0)
        actions = self.window.table_actions[table]
        self.assertTrue(actions["delete"].isEnabled())
        with patch("opldeck.ui.gui.QMessageBox.exec", return_value=QMessageBox.StandardButton.No) as confirm:
            actions["delete"].trigger()
        confirm.assert_called_once()
        self.assertTrue((self.root / entry.part_name(0)).exists())
        self.window.table_controls[table][0].setText("no matching game")
        self.assertFalse(actions["delete"].isEnabled())
        self.assertFalse(actions["rename"].isEnabled())
        self.assertFalse(actions["convert"].isEnabled())

    def test_refresh_preserves_selection_and_busy_actions_are_disabled(self):
        paths = [make_iso(self.root / "First.iso"), make_iso(self.root / "Second.iso")]
        self.window.load_isos(list(map(str, paths)))
        self.wait_scan()
        table = self.window.iso_table
        table.sortItems(2, Qt.SortOrder.AscendingOrder)
        table.selectRow(1)
        self.window.refresh_table(table)
        actions = self.window.table_actions[table]
        self.assertFalse(actions["remove"].isEnabled())
        self.assertFalse(actions["convert"].isEnabled())
        self.assertFalse(actions["refresh"].isEnabled())
        self.wait_scan()
        self.assertEqual(table.item(table.currentRow(), 2).text(), "Second")
        self.assertTrue(actions["remove"].isEnabled())
        self.assertTrue(actions["convert"].isEnabled())

    def test_rebuild_preview_requires_matching_title_and_confirmed_media(self):
        iso = make_iso(self.root / "Synthetic.iso")
        install_iso(iso, self.root, media=Media.DVD)
        cfg = self.root / "ul.cfg"
        cfg.unlink()
        dialog = RebuildDialog(scan_ul(self.root), self.window)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.save_button.isEnabled())
        include, title, media = dialog.fields[0]
        title.setText("Wrong")
        media.setCurrentText("DVD")
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIn("CRC", dialog.validation.text())
        title.setText("Synthetic")
        self.assertTrue(dialog.save_button.isEnabled())
        include.setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertFalse(cfg.exists())

    def test_rebuild_preview_existing_index_requires_confirmation(self):
        iso = make_iso(self.root / "Synthetic.iso")
        install_iso(iso, self.root, media=Media.DVD)
        cfg = self.root / "ul.cfg"
        original = cfg.read_bytes()
        dialog = RebuildDialog(scan_ul(self.root), self.window)
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.save_button.isEnabled())
        with patch("opldeck.ui.recovery_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        with patch("opldeck.ui.recovery_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        # A prévia não escreve: somente o serviço executado depois da aceitação grava.
        self.assertEqual(cfg.read_bytes(), original)
        self.assertFalse(list(self.root.glob("ul.cfg.backup-*")))

    def test_rebuild_async_scan_publish_and_refresh(self):
        iso = make_iso(self.root / "Synthetic.iso")
        entry = install_iso(iso, self.root, media=Media.DVD)
        cfg = self.root / "ul.cfg"
        cfg.unlink()

        def complete_preview(dialog):
            dialog.fields[0][1].setText("Synthetic")
            dialog.fields[0][2].setCurrentText("DVD")
            self.assertTrue(dialog.save_button.isEnabled())
            return QDialog.DialogCode.Accepted

        with patch.object(RebuildDialog, "exec", complete_preview):
            self.window.start_rebuild_scan(self.root)
            self.wait_scan()
        self.assertEqual(cfg.read_bytes(), entry.to_bytes())
        self.assertEqual(self.window.cfg_path, cfg)
        self.assertEqual(self.window.ul_table.rowCount(), 1)
        self.assertEqual(self.window.summary_counts["ok"].text(), "1")
        self.assertIn("Reconstrução UL concluída", self.window.log_view.toPlainText())
        self.assertIn("registro 1/1", self.window.transfer_details.text())

    def test_rebuild_cancelled_preview_keeps_library_unchanged(self):
        iso = make_iso(self.root / "Synthetic.iso")
        install_iso(iso, self.root, media=Media.DVD)
        cfg = self.root / "ul.cfg"
        cfg.unlink()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch.object(RebuildDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.window.start_rebuild_scan(self.root)
            self.wait_scan()
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)
        self.assertIn("nenhum arquivo", self.window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
