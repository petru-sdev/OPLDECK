"""Busca, ordenação numérica e diagnóstico UL com bibliotecas temporárias."""

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from opldeck.covers.cover_preload import CoverPreloader
from opldeck.core.formats import Media, PART_SIZE
from opldeck.ui.gui import MainWindow, STATUS_ROLE, size_text, ul_rows
from opldeck.core.ul import ULEntry
from tests.fixtures import make_iso


class LibraryUsabilityTests(unittest.TestCase):
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

    def values(self, index, size, parts='1'):
        return ['Identificado', f'SLUS_123.{index:02}', f'Jogo {index}', 'DVD',
                size_text(size), parts, str(self.root / f'Jogo {index}.iso'), str(size),
                '12345678', 'ISO9660 · SYSTEM.CNF / BOOT2', 'Pasta DVD/']

    def visible_ids(self, table):
        return [table.item(row, 1).text() for row in range(table.rowCount()) if not table.isRowHidden(row)]

    def test_size_and_bytes_sort_by_exact_value_in_every_library(self):
        sizes = [900 << 20, 1 << 30, (1 << 30) + 2048, 10 << 20, 10 << 30]
        rows = [self.values(index, size) for index, size in enumerate(sizes)]
        invalid = ['Arquivo indisponível', '—', 'Ausente', '—', '—', '—', '/missing.iso'] + ['—'] * 4
        expected = [rows[index][1] for index in sorted(range(len(rows)), key=lambda i: sizes[i])] + ['—']
        for table in (self.window.iso_table, self.window.ul_table, self.window.bin_table):
            self.window.append_rows(table, rows + [invalid])
            for column in (4, 7):
                with self.subTest(table=table.horizontalHeaderItem(4).text(), column=column):
                    table.sortItems(column, Qt.SortOrder.AscendingOrder)
                    self.assertEqual(self.visible_ids(table), expected)
                    table.sortItems(column, Qt.SortOrder.DescendingOrder)
                    self.assertEqual(self.visible_ids(table), expected[::-1])
                    # Formatação e bytes exatos continuam associados à mesma linha.
                    for row in range(1, table.rowCount()):
                        size = int(table.item(row, 7).text())
                        self.assertEqual(table.item(row, 4).text(), size_text(size))

    def test_counts_and_bin_output_sort_numerically_and_keep_selection(self):
        table = self.window.bin_table
        rows = [self.values(index, 2352 * count, str(count)) for index, count in enumerate((10, 2, 100))]
        for row, count in zip(rows, (10, 2, 100)):
            row[8] = str(2048 * count)
        self.window.append_rows(table, rows)
        selected = next(r for r in range(table.rowCount()) if table.item(r, 1).text() == rows[0][1])
        table.selectRow(selected)
        for column in (5, 8):
            table.sortItems(column, Qt.SortOrder.AscendingOrder)
            self.assertEqual(self.visible_ids(table), [rows[i][1] for i in (1, 0, 2)])
            self.assertEqual(self.window._selected_values(table)[0][1], rows[0][1])
        self.window.append_rows(table, [self.values(3, 2352 * 3, '3')])
        table.sortItems(5, Qt.SortOrder.AscendingOrder)
        self.assertEqual([table.item(r, 5).text() for r in range(table.rowCount())], ['2', '3', '10', '100'])

    def test_search_matches_accents_spacing_and_serial_variants(self):
        dvd = self.root / 'DVD'
        dvd.mkdir()
        first = make_iso(dvd / 'Ação rápida.iso', 'SLUS_123.45')
        second = make_iso(dvd / 'Outra ação.iso', 'SLUS_222.22')
        self.window.load_isos([str(first), str(second)])
        self.scan_finished()
        table = self.window.iso_table
        table.sortItems(2, Qt.SortOrder.DescendingOrder)
        search, _, label = self.window.table_controls[table]
        for query in ('acao rapida', 'AÇÃO RÁPIDA', '  acao   rapida ', 'ac\u0327a\u0303o rapida',
                      'SLUS-12345', 'slus12345', 'slus_123.45', str(first).upper()):
            with self.subTest(query=query):
                search.setText(query)
                self.assertEqual(self.visible_ids(table), ['SLUS_123.45'])
                self.assertEqual(label.text(), '1 de 2 jogos')
        search.setText('outra rapida')
        self.assertEqual(self.visible_ids(table), [])
        search.setText('   ')
        self.assertEqual(len(self.visible_ids(table)), 2)

    def test_search_refreshes_aliases_and_preserves_status_filter_and_files(self):
        dvd = self.root / 'DVD'
        dvd.mkdir()
        path = make_iso(dvd / 'Ação.iso', 'SLUS_123.45')
        self.window.load_isos([str(path), str(dvd / 'Ausente.iso')])
        self.scan_finished()
        table = self.window.iso_table
        search, status_filter, _ = self.window.table_controls[table]
        search.setText('acao')
        self.assertEqual(self.visible_ids(table), ['SLUS_123.45'])
        table.selectAll()
        status_filter.setCurrentIndex(status_filter.findData('error'))
        self.assertEqual(self.visible_ids(table), [])
        self.assertEqual(self.window._selected_rows(table), [])
        self.assertFalse(self.window.table_actions[table]['remove'].isEnabled())
        status_filter.setCurrentIndex(status_filter.findData('warning'))
        make_iso(path, 'SLUS_333.33')
        self.window.refresh_table(table)
        self.scan_finished()
        search.setText('slus12345')
        self.assertEqual(self.visible_ids(table), [])
        search.setText('slus33333')
        self.assertEqual(self.visible_ids(table), ['SLUS_333.33'])
        table.selectAll()
        self.window.remove_selected(table)
        self.assertTrue(path.is_file())
        self.assertEqual(table.rowCount(), 1)

    def test_invalid_ul_part_blocks_export_but_keeps_other_games_visible(self):
        entries = [ULEntry('Link', 'SLUS_123.45', 1, Media.DVD),
                   ULEntry('Regular', 'SLUS_222.22', 1, Media.DVD)]
        cfg = self.root / 'ul.cfg'
        cfg.write_bytes(b''.join(entry.to_bytes() for entry in entries))
        regular = self.root / entries[1].part_name(0)
        regular.write_bytes(b'x' * 2048)
        link = self.root / entries[0].part_name(0)
        link.symlink_to(regular)
        self.window.load_cfg(cfg)
        self.scan_finished()
        table = self.window.ul_table
        self.assertEqual(table.rowCount(), 2)
        row = next(r for r in range(2) if table.item(r, 1).text() == entries[0].game_id)
        table.selectRow(row)
        self.assertEqual(table.item(row, 0).data(STATUS_ROLE), 'error')
        self.assertFalse(self.window.table_actions[table]['convert'].isEnabled())
        self.assertFalse(self.window.convert_buttons[table].isEnabled())
        self.assertIn(link.name, self.window.details.toPlainText())
        self.assertTrue(link.is_symlink())
        self.assertEqual(regular.read_bytes(), b'x' * 2048)

    def test_conversion_buttons_follow_selection_filter_and_busy_state(self):
        self.window.cfg_path = self.root / 'ul.cfg'
        for table in (self.window.ul_table, self.window.bin_table):
            self.window.show_table(table)
            good = self.values(0, 2048)
            good[0] = 'Partes presentes' if table is self.window.ul_table else 'Identificado (verificação parcial)'
            self.window.append_rows(table, [good])
            self.window.filter_table(table)
            button = self.window.convert_buttons[table]
            self.assertFalse(button.isEnabled())
            table.selectRow(0)
            self.assertTrue(button.isEnabled())
            self.window.worker = object()
            self.window._update_actions()
            self.assertFalse(button.isEnabled())
            self.window.worker = None
            self.window._busy(False)
            self.assertTrue(button.isEnabled())
            search = self.window.table_controls[table][0]
            search.setText('sem correspondência')
            self.assertFalse(button.isEnabled())
            search.clear()
            self.assertFalse(button.isEnabled())


class ULPartDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cfg = self.root / 'ul.cfg'

    def entry(self, title='Teste', game_id='SLUS_123.45', parts=1):
        entry = ULEntry(title, game_id, parts, Media.DVD)
        with self.cfg.open('ab') as stream:
            stream.write(entry.to_bytes())
        return entry

    def test_special_files_and_dangling_symlink_are_diagnosed_without_reading(self):
        for index, kind in enumerate(('directory', 'fifo', 'symlink')):
            entry = self.entry(kind, f'SLUS_123.{index:02}')
            path = self.root / entry.part_name(0)
            if kind == 'directory':
                path.mkdir()
            elif kind == 'fifo':
                os.mkfifo(path)
            else:
                path.symlink_to(self.root / 'missing-target')
        for row in ul_rows(str(self.cfg)):
            self.assertIn('Não foi possível ler a parte 00', row[0])
            self.assertIn(Path(row[6]).name, row[0])
            self.assertEqual(row[7], '0')

    def test_part_access_error_does_not_hide_remaining_games(self):
        first = self.entry()
        second = self.entry('Segundo', 'SLUS_222.22')
        for entry in (first, second):
            (self.root / entry.part_name(0)).write_bytes(b'x' * 2048)
        original = os.open

        def denied(path, *args, **kwargs):
            if Path(path).name == first.part_name(0):
                raise PermissionError('acesso negado para teste')
            return original(path, *args, **kwargs)

        with patch('opldeck.library.fileio.os.open', side_effect=denied):
            rows = ul_rows(str(self.cfg))
        self.assertEqual(len(rows), 2)
        self.assertIn('acesso negado', rows[0][0])
        self.assertIn(first.part_name(0), rows[0][0])
        self.assertEqual(rows[1][0], 'Partes presentes')

    def test_missing_and_invalid_parts_are_reported_together(self):
        entry = self.entry(parts=3)
        (self.root / entry.part_name(1)).write_bytes(b'x' * 2048)
        (self.root / entry.part_name(2)).write_bytes(b'x' * 2048)
        row = ul_rows(str(self.cfg))[0]
        self.assertIn('Partes ausentes: 00', row[0])
        self.assertIn('Tamanho de parte inválido', row[0])
        self.assertIn('01 (2048 bytes)', row[0])
        self.assertNotIn('02 (2048 bytes)', row[0])
        self.assertEqual(row[7], '4096')

    def test_valid_sparse_boundary_and_pending_transaction_keep_diagnostics(self):
        entry = self.entry(parts=2)
        with (self.root / entry.part_name(0)).open('wb') as stream:
            stream.truncate(PART_SIZE)
        (self.root / entry.part_name(1)).write_bytes(b'x' * 2048)
        self.assertEqual(ul_rows(str(self.cfg))[0][0], 'Partes presentes')
        (self.root / '.opldeck-transaction').mkdir()
        (self.root / entry.part_name(1)).unlink()
        status = ul_rows(str(self.cfg))[0][0]
        self.assertTrue(status.startswith('Operação pendente'))
        self.assertIn('Partes ausentes: 01', status)
