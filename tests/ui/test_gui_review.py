"""Regressões de navegação e atualização de bibliotecas grandes."""

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from opldeck.covers.cover_preload import CoverPreloader
from opldeck.library.devices import Volume
from opldeck.ui.gui import MainWindow
from tests.fixtures import make_iso


class GUIReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.window = MainWindow(auto_devices=False, cover_preloader=CoverPreloader(settings=settings))
        self.window.import_monitor.timer.stop()
        self.window.storage_timer.stop()
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.close)

    def close(self):
        self.window.pool.waitForDone(5000)
        self.app.processEvents()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def scan_finished(self):
        self.assertTrue(self.window.pool.waitForDone(5000))
        self.app.processEvents()
        self.assertIsNone(self.window.worker)

    def rows(self, count):
        return [['Identificado', f'SLUS_123.{i % 100:02}', f'Jogo {i:04}', 'DVD', '1.00 GiB', '1',
                 str(self.root / f'Jogo {i:04}.iso'), '1073741824', '12345678',
                 'ISO9660 · SYSTEM.CNF / BOOT2', 'Pasta DVD/'] for i in range(count)]

    def test_downloads_before_iso_and_actions_follow_page_after_reordering(self):
        window = self.window
        self.assertEqual([window.tabs.tabText(i) for i in range(window.tabs.count())],
                         ['Downloads', 'Imagens ISO', 'Jogos UL', 'Imagens BIN/CUE', 'Fila de transferências', 'Log', 'Sobre'])
        self.assertIs(window.active_table(), window.iso_table)
        # A future reorder must not change which library Ctrl+O and F5 target.
        window.tabs.tabBar().moveTab(1, 3)
        for table in (window.iso_table, window.ul_table, window.bin_table):
            window.show_table(table)
            self.assertIs(window.active_table(), table)
            with patch.object(window.table_actions[table]['add'], 'trigger') as opening:
                window.open_current()
                opening.assert_called_once_with()
            with patch.object(window, 'refresh_table') as refresh:
                window.window_actions[2].trigger()
                refresh.assert_called_once_with(table)
        window.details.setPlainText('Dados da seleção anterior')
        window.tabs.setCurrentWidget(window.downloads_page)
        self.assertIsNone(window.active_table())
        self.assertTrue(window.summary.isHidden())
        self.assertEqual(window.details.toPlainText(), '')
        self.assertEqual(window.downloads_status.text(), 'Em breve')
        with patch('opldeck.ui.gui.QFileDialog.getOpenFileNames') as picker:
            window.open_current()
            window.focus_search()
            window.window_actions[2].trigger()
            picker.assert_not_called()
        self.assertIsNone(window.worker)

    def test_search_after_sort_append_and_refresh_keeps_titles_ids_and_paths_aligned(self):
        directory = self.root / 'DVD'
        directory.mkdir()
        first = make_iso(directory / 'Zelda.iso', 'SLUS_111.11')
        second = make_iso(directory / 'Árvore.iso', 'SLUS_222.22')
        self.window.load_isos([str(first), str(second)])
        self.scan_finished()
        table = self.window.iso_table
        table.sortItems(2, Qt.SortOrder.DescendingOrder)
        search, _, label = self.window.table_controls[table]
        for query, expected in [('ÁRVORE', second), ('slus_111.11', first), (str(second).upper(), second)]:
            search.setText(query)
            self.assertEqual(label.text(), '1 de 2 jogos')
            visible = [table.item(r, 6).text() for r in range(table.rowCount()) if not table.isRowHidden(r)]
            self.assertEqual(visible, [str(expected)])
        make_iso(second, 'SLUS_333.33')
        self.window.refresh_table(table)
        self.scan_finished()
        search.setText('SLUS_222.22')
        self.assertEqual(label.text(), '0 de 2 jogos')
        search.setText('SLUS_333.33')
        self.assertEqual(label.text(), '1 de 2 jogos')
        self.assertTrue(table.isSortingEnabled())
        self.assertTrue(table.updatesEnabled())

    def test_large_selection_removal_preserves_hidden_rows_and_updates_counters(self):
        table = self.window.iso_table
        rows = self.rows(400)
        self.window.imported.update(row[6] for row in rows)
        self.window.append_rows(table, rows)
        self.window.filter_table(table)
        table.selectAll()
        self.assertIn('400 jogos selecionados', self.window.details.toPlainText())
        search, _, label = self.window.table_controls[table]
        search.setText('Jogo 00')
        table.selectAll()
        self.window.remove_selected(table)
        self.assertEqual(table.rowCount(), 300)
        self.assertEqual(len(self.window.imported), 300)
        self.assertEqual(label.text(), '0 de 300 jogos')
        self.assertEqual(self.window.summary_counts['total'].text(), '300')
        self.assertFalse(self.window.table_actions[table]['remove'].isEnabled())
        search.clear()
        self.assertEqual(label.text(), '300 de 300 jogos')
        self.assertEqual({table.item(r, 6).text() for r in range(table.rowCount())}, self.window.imported)

    def test_usb_button_checks_visible_local_candidates_and_keeps_selection_order(self):
        table = self.window.iso_table
        rows = self.rows(4)
        volume = Volume(self.root / 'USB', '/dev/test1', 'Test', '1')
        rows[1][6] = str(volume.root / 'DVD' / 'Remote.iso')
        rows[3][1] = '—'
        rows[3][0] = 'Arquivo inválido'
        self.window.append_rows(table, rows)
        self.window.filter_table(table)
        self.assertFalse(self.window.usb_start.isEnabled())
        self.window.active_volume = volume
        self.window.volume_ready = True
        self.window._update_actions()
        self.assertFalse(self.window.usb_start.isEnabled())
        search, status_filter, _ = self.window.table_controls[table]
        status_filter.setCurrentIndex(status_filter.findData('ok'))
        self.assertTrue(self.window.usb_start.isEnabled())
        self.assertEqual({r[6] for r in self.window.usb_values()}, {rows[0][6], rows[2][6]})
        table.sortItems(2, Qt.SortOrder.DescendingOrder)
        table.selectAll()
        self.assertEqual([r[6] for r in self.window.usb_values()], [rows[2][6], rows[0][6]])
        search.setText('Remote.iso')
        self.assertFalse(self.window.usb_start.isEnabled())
        self.assertEqual(self.window.usb_values(), [])
        self.window.worker = object()
        self.window._update_actions()
        self.assertFalse(self.window.usb_start.isEnabled())
        self.window.worker = None

    def test_bulk_log_preserves_levels_multiline_messages_and_ten_thousand_line_limit(self):
        self.window.log_view.clear()
        self.window.log_many([('INFO', f'Registro {i}') for i in range(10_020)] +
                             [('WARNING', 'Primeiro aviso\nSegundo aviso'), ('ERROR', 'Falha final')])
        lines = self.window.log_view.toPlainText().splitlines()
        self.assertEqual(len(lines), 10_000)
        self.assertTrue(lines[0].endswith('[INFO] Registro 23'))
        self.assertTrue(lines[-3].endswith('[WARNING] Primeiro aviso'))
        self.assertTrue(lines[-2].endswith('[WARNING] Segundo aviso'))
        self.assertTrue(lines[-1].endswith('[ERROR] Falha final'))
        self.window.log('INFO', 'Mensagem posterior')
        self.assertEqual(self.window.log_view.document().blockCount(), 10_000)
        self.assertTrue(self.window.log_view.toPlainText().endswith('[INFO] Mensagem posterior'))
