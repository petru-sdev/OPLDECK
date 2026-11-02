"""Regressões de compatibilidade, sempre em bibliotecas sintéticas temporárias."""

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from opldeck.library.diagnostics import iso_name_parts, iso_warnings
from opldeck.core.formats import FormatError, Media
from opldeck.core.iso import inspect_iso
from tests.fixtures import make_iso

try:
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QMessageBox
    from opldeck.covers.artwork import cover_state, decode_image, encode_image, load_cover, prepare_cover, save_cover
    from opldeck.ui.game_gui import GameDialog
    from opldeck.ui.gui import iso_rows, row_status
except ModuleNotFoundError:
    QApplication = None


class ISONameTests(unittest.TestCase):
    def test_title_limit_counts_bytes_and_excludes_legacy_prefix(self):
        for prefix in ('', 'SLUS_123.45.'):
            for title, warns in [('a' * 160, False), ('a' * 161, True), ('é' * 80, False), ('é' * 81, True)]:
                with self.subTest(prefix=prefix, title=title):
                    warnings = '\n'.join(iso_warnings(Path('DVD') / f'{prefix}{title}.iso', 'SLUS_123.45'))
                    self.assertEqual('Título ISO com' in warnings, warns)
                    if warns:
                        self.assertIn(f'{len(os.fsencode(title))} bytes', warnings)
                    else:
                        self.assertEqual(warnings, '')

    def test_legacy_detection_follows_byte_positions_used_by_opl(self):
        self.assertEqual(iso_name_parts('SLUS_123.45.Game.ISO'), ('Game', 'SLUS_123.45'))
        self.assertEqual(iso_name_parts('ÉAAA_123.45.Game.iso'), ('ÉAAA_123.45.Game', ''))
        # OPL recognizes the separators even if the startup itself is malformed.
        self.assertIn('nome=ABCD_ABC.DE', '\n'.join(iso_warnings('DVD/ABCD_ABC.DE.Game.iso', 'SLUS_123.45')))

    def test_legacy_id_and_case_difference_report_both_values(self):
        for startup in ('SLUS_999.99', 'slus_123.45'):
            warnings = '\n'.join(iso_warnings(f'DVD/{startup}.Game.iso', 'SLUS_123.45'))
            self.assertIn(f'nome={startup}', warnings)
            self.assertIn('interno=SLUS_123.45', warnings)
        self.assertEqual(iso_warnings('DVD/SLUS_123.45.Game.iso', 'SLUS_123.45'), ())

    def test_folder_warnings_distinguish_source_from_console_layout(self):
        for folder in ('CD', 'DVD'):
            self.assertEqual(iso_warnings(f'{folder}/Game.iso', 'SLUS_123.45'), ())
        for folder in ('cd', 'Dvd'):
            warning = '\n'.join(iso_warnings(f'{folder}/Game.iso', 'SLUS_123.45'))
            self.assertIn(f'use o nome {folder.upper()}', warning)
        warning = '\n'.join(iso_warnings('Downloads/Game.iso', 'SLUS_123.45'))
        self.assertIn('fora de CD/DVD', warning)
        self.assertIn('Pode ser convertida para UL', warning)

    def test_unsafe_characters_and_trailing_title_are_diagnosed(self):
        for character in '<>:"\\|?*\n\x7f':
            with self.subTest(character=repr(character)):
                self.assertIn('FAT/exFAT', '\n'.join(iso_warnings(f'DVD/Game{character}.iso', 'SLUS_123.45')))
        for title in ('Game ', 'Game.'):
            self.assertIn('termina com espaço ou ponto', '\n'.join(iso_warnings(f'DVD/{title}.iso', 'SLUS_123.45')))

    def test_inspection_keeps_game_data_and_warnings_clear_after_user_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = make_iso(root / 'SLUS_999.99.Game.iso')
            original = path.read_bytes()
            game = inspect_iso(path)
            self.assertEqual(game.game_id, 'SLUS_123.45')
            self.assertEqual(game.title, 'Game')
            self.assertIn('ID no nome', game.warning)
            self.assertIn('fora de CD/DVD', game.warning)
            self.assertEqual(path.read_bytes(), original)
            (root / 'DVD').mkdir()
            target = path.rename(root / 'DVD' / 'SLUS_123.45.Game.iso')
            corrected = inspect_iso(target)
            self.assertEqual(corrected.warning, '')
            self.assertEqual(corrected.media, Media.DVD)
            self.assertEqual(target.read_bytes(), original)


@unittest.skipIf(QApplication is None, 'PySide6 não instalado')
class OPLCoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.art = self.root / 'ART'
        self.art.mkdir()
        self.game_id = 'SLUS_123.45'

    def image(self, width=1024, height=2048):
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor(120, 40, 200, 100))
        return image

    def write(self, name, image=None, suffix='.png'):
        path = self.art / name
        path.write_bytes(encode_image(image if image is not None else self.image(), suffix))
        return path

    def dialog(self):
        dvd = self.root / 'DVD'
        dvd.mkdir(exist_ok=True)
        path = make_iso(dvd / 'Game.iso')
        dialog = GameDialog(iso_rows([str(path)])[0], 'ISO', '')
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_proportional_preparation_keeps_alpha_and_never_upscales(self):
        image = self.image()
        prepared = prepare_cover(image)
        self.assertEqual((prepared.width(), prepared.height()), (256, 512))
        self.assertEqual(prepared.pixelColor(10, 10).alpha(), 100)
        self.assertEqual((image.width(), image.height()), (1024, 2048))
        small = self.image(120, 180)
        self.assertIs(prepare_cover(small), small)
        for width, height in ((4096, 1), (1, 4096)):
            scaled = prepare_cover(self.image(width, height))
            self.assertFalse(scaled.isNull())
            self.assertLessEqual(max(scaled.width(), scaled.height()), 512)

    def test_save_enforces_png_envelope_even_without_preview(self):
        image = self.image(2048, 1024)
        target = save_cover(self.root, self.game_id, image, cover_state(self.root, self.game_id))
        data = target.read_bytes()
        self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
        # PNG IHDR: 8-bit RGBA, rather than a source-dependent palette/bit depth.
        self.assertEqual(data[24:26], b'\x08\x06')
        saved = decode_image(data)
        self.assertEqual((saved.width(), saved.height()), (512, 256))
        self.assertEqual(saved.pixelColor(10, 10).alpha(), 100)

    def test_legacy_preparation_waits_for_save_and_canonical_wins_on_reopen(self):
        source = self.write(f'{self.game_id}_COV.jpg', suffix='.jpg')
        original = source.read_bytes()
        dialog = self.dialog()
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertTrue(dialog.preloaded_pending)
        self.assertEqual(list(self.art.iterdir()), [source])
        self.assertEqual(dialog.pending_image.height(), 512)
        with patch('opldeck.ui.game_gui.QMessageBox.question', side_effect=AssertionError('No overwrite')):
            dialog.save()
        self.assertEqual(source.read_bytes(), original)
        loaded = load_cover(self.root, self.game_id)
        self.assertEqual(loaded.path.name, f'{self.game_id}_COV.png')
        self.assertFalse(loaded.needs_save)
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIsNone(self.dialog().pending_image)

    def test_discard_and_close_auto_preparation_never_write_or_prompt(self):
        source = self.write(f'{self.game_id}_COV.jpg', suffix='.jpg')
        original = source.read_bytes()
        dialog = self.dialog()
        with patch('opldeck.ui.game_gui.QMessageBox.question', side_effect=AssertionError('Auto preview')):
            dialog.discard()
            self.assertIsNone(dialog.pending_image)
            self.assertFalse(dialog.save_button.isEnabled())
            self.dialog().reject()
        self.assertEqual(list(self.art.iterdir()), [source])
        self.assertEqual(source.read_bytes(), original)

    def test_existing_large_png_requires_confirmation_before_resize(self):
        target = self.write(f'{self.game_id}_COV.png')
        original = target.read_bytes()
        dialog = self.dialog()
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertEqual(target.read_bytes(), original)
        with patch('opldeck.ui.game_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            dialog.save()
        self.assertEqual(target.read_bytes(), original)
        with patch('opldeck.ui.game_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            dialog.save()
        self.assertEqual(decode_image(target.read_bytes()).height(), 512)
        self.assertFalse(dialog.save_button.isEnabled())

    def test_jpeg_content_with_png_extension_is_prepared(self):
        source = self.write(f'{self.game_id}_COV.png', self.image(80, 120), suffix='.jpg')
        original = source.read_bytes()
        loaded = load_cover(self.root, self.game_id)
        self.assertTrue(loaded.needs_save)
        self.assertEqual(source.read_bytes(), original)
        save_cover(self.root, self.game_id, loaded.image, loaded.state)
        self.assertTrue(source.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))

    def test_migration_refuses_changed_source_or_new_canonical_destination(self):
        source = self.write(f'{self.game_id}_COV.jpg', suffix='.jpg')
        state = cover_state(self.root, self.game_id)
        source.write_bytes(b'changed after preview')
        with self.assertRaisesRegex(FormatError, 'mudou'):
            save_cover(self.root, self.game_id, self.image(), state)
        self.assertFalse(state.path.exists())
        state = cover_state(self.root, self.game_id)
        state.path.write_bytes(b'created by another program')
        with self.assertRaisesRegex(FormatError, 'mudou'):
            save_cover(self.root, self.game_id, self.image(), state)
        self.assertEqual(state.path.read_bytes(), b'created by another program')
        self.assertEqual(source.read_bytes(), b'changed after preview')

    def test_symlink_canonical_is_refused_even_with_valid_legacy_source(self):
        self.write(f'{self.game_id}_COV.jpg', suffix='.jpg')
        outside = self.root / 'outside.png'
        outside.write_bytes(b'preserved')
        (self.art / f'{self.game_id}_COV.png').symlink_to(outside)
        with self.assertRaises(OSError):
            cover_state(self.root, self.game_id)
        self.assertIsNone(load_cover(self.root, self.game_id).state)
        self.assertEqual(outside.read_bytes(), b'preserved')

    def test_name_and_folder_warnings_reach_gui_without_blocking_identification(self):
        path = make_iso(self.root / 'SLUS_999.99.Game.iso')
        values = iso_rows([str(path)])[0]
        self.assertEqual(values[1], self.game_id)
        severity, detail = row_status(values, False)
        self.assertEqual(severity, 'warning')
        self.assertIn('ID no nome', detail)
        self.assertIn('fora de CD/DVD', detail)
