import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from opldeck.covers.artwork import (
        MAX_IMAGE_BYTES, _regular_reader, cover_state, decode_image, encode_image, image_bytes, library_root, save_cover,
    )
    from opldeck.ui.game_gui import GameDialog
    from opldeck.ui.gui import MainWindow, iso_rows
except ModuleNotFoundError:
    QApplication = None

from opldeck.core.conversion import TRANSACTION_DIR, directory_lock, install_iso
from opldeck.core.formats import FormatError, Media
from opldeck.core.ul import read_cfg
from tests.fixtures import make_iso


@unittest.skipIf(QApplication is None, "PySide6 não instalado")
class ArtworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game_id = "SLUS_123.45"
        self.image = QImage(120, 180, QImage.Format.Format_ARGB32)
        self.image.fill(QColor("red"))

    def make_cover(self, suffix=".png"):
        art = self.root / "ART"
        art.mkdir(exist_ok=True)
        target = art / (self.game_id + "_COV" + suffix)
        target.write_bytes(encode_image(self.image, suffix))
        return target

    def dialog(self, **kwargs):
        dvd = self.root / "DVD"
        dvd.mkdir(exist_ok=True)
        path = make_iso(dvd / "Synthetic.iso")
        dialog = GameDialog(iso_rows([str(path)])[0], "ISO", "Dados técnicos", **kwargs)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_new_cover_canonical_png_preserves_game(self):
        iso = make_iso(self.root / "Synthetic.iso")
        before = iso.read_bytes()
        state = cover_state(self.root, self.game_id)
        self.assertFalse((self.root / "ART").exists())
        path = save_cover(self.root, self.game_id, self.image, state)
        self.assertEqual(path, self.root / "ART" / "SLUS_123.45_COV.png")
        self.assertTrue(path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(decode_image(image_bytes(path)).size(), self.image.size())
        self.assertEqual(iso.read_bytes(), before)
        self.assertFalse(list(path.parent.glob(".opldeck-cover-*")))

    def test_open_reads_cover_once_and_scans_each_directory_once(self):
        path = self.make_cover()
        scans = []
        iterdir = Path.iterdir

        def listing(directory):
            scans.append(directory)
            return iterdir(directory)

        with patch('pathlib.Path.iterdir', listing), \
                patch('opldeck.covers.artwork._regular_reader', wraps=_regular_reader) as reads:
            dialog = self.dialog()
        self.assertEqual(scans, [self.root, path.parent])
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(dialog.state, cover_state(self.root, self.game_id))
        self.assertEqual(dialog.current_image, decode_image(path.read_bytes()))

    def test_local_preview_does_not_reread_or_accept_external_cover_change(self):
        path = self.make_cover()
        dialog = self.dialog()
        state = dialog.state
        source = self.root / 'new.png'
        source.write_bytes(encode_image(self.image, '.png'))
        changed = b'external change after opening'
        path.write_bytes(changed)
        with patch('opldeck.ui.game_gui.QFileDialog.getOpenFileName', return_value=(str(source), '')), \
                patch('opldeck.covers.artwork._regular_reader', wraps=_regular_reader) as reads, \
                patch('pathlib.Path.iterdir', side_effect=AssertionError('Unexpected library scan')):
            dialog.choose_cover()
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(reads.call_args.args[0], source)
        self.assertIs(dialog.state, state)
        self.assertEqual(dialog.current_image, self.image)
        self.assertTrue(dialog.save_button.isEnabled())
        with patch('opldeck.ui.game_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes), \
                patch('opldeck.ui.game_gui.QMessageBox.warning') as warning:
            dialog.save()
        self.assertIn('mudou', warning.call_args.args[2])
        self.assertEqual(path.read_bytes(), changed)
        self.assertIsNotNone(dialog.pending_image)

    def test_corrupt_exact_cover_can_preview_legacy_and_replace_exact(self):
        path = self.make_cover()
        path.write_bytes(b'broken image')
        legacy = path.parent / 'Synthetic_COV.png'
        data = encode_image(self.image, '.png')
        legacy.write_bytes(data)
        dialog = self.dialog()
        self.assertIsNotNone(dialog.pending_image)
        self.assertIn(legacy.name, dialog.cover_status.text())
        self.assertEqual(dialog.state.path, path)
        self.assertIn(path.name, dialog.cover_status.text())
        dialog.pending_image = self.image
        with patch('opldeck.ui.game_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            dialog.save()
        self.assertEqual(legacy.read_bytes(), data)
        self.assertFalse(decode_image(path.read_bytes()).isNull())

    def test_existing_lowercase_art_uppercase_jpeg_is_found_and_preserved(self):
        art = self.root / 'art'
        art.mkdir()
        path = art / 'slus_123.45_cov.JPEG'
        path.write_bytes(encode_image(self.image, '.jpg'))
        original = path.read_bytes()
        dialog = self.dialog()
        self.assertEqual(dialog.state.origin, path)
        self.assertIsNotNone(dialog.pending_image)
        self.assertFalse(dialog.state.path.exists())
        self.assertTrue(dialog.zoom_button.isEnabled())
        state = cover_state(self.root, self.game_id)
        saved = save_cover(self.root, self.game_id, self.image, state)
        self.assertEqual(saved, art / f'{self.game_id}_COV.png')
        self.assertTrue(saved.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.root / 'ART').exists())

    def test_serial_hyphen_cover_is_recognized(self):
        art = self.root / 'ART'
        art.mkdir()
        path = art / 'SLUS-12345_COV.PNG'
        path.write_bytes(encode_image(self.image, '.png'))
        self.assertEqual(cover_state(self.root, self.game_id).origin, path)
        dialog = self.dialog()
        self.assertIsNotNone(dialog.pending_image)
        self.assertEqual(dialog.state.path.name, f'{self.game_id}_COV.png')

    def test_legacy_title_preview_saves_canonical_without_overwriting_original(self):
        art = self.root / 'ART'
        art.mkdir()
        path = art / 'Synthetic_COV.png'
        original = encode_image(self.image, '.png')
        path.write_bytes(original)
        dialog = self.dialog()
        self.assertIsNotNone(dialog.pending_image)
        self.assertIn('Nome legado', dialog.cover_status.text())
        dialog.pending_image = self.image
        dialog.save()
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue((art / f'{self.game_id}_COV.png').is_file())

    def test_duplicate_covers_allow_viewing_but_block_ambiguous_save(self):
        self.make_cover('.jpg')
        self.make_cover('.jpeg')
        dialog = self.dialog()
        self.assertIsNotNone(dialog.current_image)
        self.assertIsNone(dialog.state)
        dialog.pending_image = self.image
        dialog.reload_cover()
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertIn('apenas uma', dialog.cover_status.text())

    def test_art_case_collision_does_not_choose_arbitrary_directory(self):
        self.make_cover()
        (self.root / 'art').mkdir()
        with self.assertRaisesRegex(FormatError, 'mais de uma pasta'):
            cover_state(self.root, self.game_id)

    def test_loose_iso_uses_connected_library_and_guard_before_save(self):
        self.make_cover()
        source = make_iso(self.root / 'Synthetic.iso')
        def guard(root, write):
            if write:
                raise ValueError('Pendrive removido')
        dialog = GameDialog(iso_rows([str(source)])[0], 'ISO', '', library=self.root, library_guard=guard)
        self.addCleanup(dialog.deleteLater)
        self.assertIsNotNone(dialog.current_image)
        before = dialog.current_cover_path.read_bytes()
        dialog.pending_image = self.image
        with patch('opldeck.ui.game_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes), \
                patch('opldeck.ui.game_gui.QMessageBox.warning') as warning:
            dialog.save()
        warning.assert_called_once()
        self.assertEqual(dialog.current_cover_path.read_bytes(), before)

    def test_cover_for_other_id_is_not_used(self):
        path = self.make_cover()
        path.rename(path.parent / 'SLUS_543.21_COV.jpg')
        dialog = self.dialog()
        self.assertIsNone(dialog.current_image)
        self.assertIn('Nenhuma capa correspondente', dialog.cover_status.text())

    def test_png_replacement_and_jpeg_transparency(self):
        path = self.make_cover(".png")
        self.image.fill(QColor("transparent"))
        state = cover_state(self.root, self.game_id)
        save_cover(self.root, self.game_id, self.image, state)
        self.assertEqual(decode_image(image_bytes(path)).pixelColor(0, 0).alpha(), 0)
        jpeg = decode_image(encode_image(self.image, ".jpg"))
        self.assertGreater(jpeg.pixelColor(0, 0).red(), 250)
        self.assertFalse(path.with_suffix(".jpg").exists())

    def test_reject_invalid_format_size_and_id(self):
        for data in (b"fake image", b"x" * (MAX_IMAGE_BYTES + 1)):
            with self.assertRaises(FormatError):
                decode_image(data)
        wide = QImage(4097, 1, QImage.Format.Format_RGB32)
        path = self.root / "large.png"
        wide.save(str(path), "PNG")
        with self.assertRaises(FormatError):
            decode_image(image_bytes(path))
        with self.assertRaises(FormatError):
            cover_state(self.root, "../../outside")
        self.assertFalse((self.root / "ART").exists())

    def test_external_change_and_alternate_extension_preserved(self):
        path = self.make_cover()
        state = cover_state(self.root, self.game_id)
        path.write_bytes(b"externally changed")
        with self.assertRaisesRegex(FormatError, "mudou"):
            save_cover(self.root, self.game_id, self.image, state)
        self.assertEqual(path.read_bytes(), b"externally changed")
        alternate = self.make_cover(".jpg")
        original = alternate.read_bytes()
        save_cover(self.root, self.game_id, self.image, cover_state(self.root, self.game_id))
        self.assertEqual(alternate.read_bytes(), original)
        self.assertFalse(decode_image(path.read_bytes()).isNull())

    def test_symbolic_links_refused(self):
        external = self.root / "external"
        external.mkdir()
        (self.root / "ART").symlink_to(external, target_is_directory=True)
        with self.assertRaises(FormatError):
            cover_state(self.root, self.game_id)
        (self.root / "ART").unlink()
        (self.root / "ART").mkdir()
        target = self.root / "ART" / f"{self.game_id}_COV.jpg"
        target.symlink_to(external / "missing.jpg")
        with self.assertRaises(OSError):
            cover_state(self.root, self.game_id)
        self.assertEqual(list(external.iterdir()), [])

    def test_failed_publication_and_sync_preserve_old_cover(self):
        path = self.make_cover()
        before = path.read_bytes()
        state = cover_state(self.root, self.game_id)
        for target in ("opldeck.covers.artwork.os.replace", "opldeck.covers.artwork.os.fsync"):
            with patch(target, side_effect=OSError("falha")), self.assertRaises(OSError):
                save_cover(self.root, self.game_id, self.image, state)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(list(path.parent.glob(".opldeck-cover-*")))

    def test_new_destination_collision_is_not_overwritten(self):
        state = cover_state(self.root, self.game_id)
        from opldeck.library.management import _move

        def race(source, target):
            target.write_bytes(b"other writer")
            _move(source, target)

        with patch("opldeck.covers.artwork._move", side_effect=race), self.assertRaises(FileExistsError):
            save_cover(self.root, self.game_id, self.image, state)
        self.assertEqual(state.path.read_bytes(), b"other writer")
        self.assertFalse(list(state.path.parent.glob(".opldeck-cover-*")))

    def test_pending_management_and_directory_lock_block_save(self):
        state = cover_state(self.root, self.game_id)
        with directory_lock(self.root), self.assertRaisesRegex(FormatError, "Outra operação"):
            save_cover(self.root, self.game_id, self.image, state)
        (self.root / TRANSACTION_DIR).mkdir()
        with self.assertRaisesRegex(FormatError, "pendente"):
            save_cover(self.root, self.game_id, self.image, state)
        self.assertFalse((self.root / "ART").exists())

    def test_root_detection_never_guesses_loose_image_library(self):
        self.assertEqual(library_root(self.root / "DVD" / "Game.iso", "ISO"), self.root)
        self.assertEqual(library_root(self.root / "ul.part.00", "UL"), self.root)
        self.assertIsNone(library_root(self.root / "Game.iso", "ISO"))
        self.assertIsNone(library_root(self.root / "DVD" / "Game.bin", "BIN/CUE"))

    def test_dialog_preview_cancel_and_save(self):
        source = self.root / "chosen.png"
        source.write_bytes(encode_image(self.image, ".png"))
        dialog = self.dialog()
        with patch("opldeck.ui.game_gui.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            dialog.choose_cover()
        self.assertFalse((self.root / "ART").exists())
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.show()
        with patch("opldeck.ui.game_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            dialog.reject()
        self.assertTrue(dialog.isVisible())
        dialog.save()
        self.assertTrue(dialog.saved_paths[0].is_file())
        self.assertIsNone(dialog.pending_image)
        dialog.reject()
        self.assertFalse(dialog.isVisible())

    def test_dialog_replace_declined_and_invalid_source_preserve_cover(self):
        path = self.make_cover()
        before = path.read_bytes()
        dialog = self.dialog()
        dialog.pending_image = self.image
        with patch("opldeck.ui.game_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            dialog.save()
        self.assertEqual(path.read_bytes(), before)
        invalid = self.root / "invalid.jpg"
        invalid.write_text("broken")
        with patch("opldeck.ui.game_gui.QFileDialog.getOpenFileName", return_value=(str(invalid), "")), \
                patch("opldeck.ui.game_gui.QMessageBox.warning") as warning:
            dialog.choose_cover()
        warning.assert_called_once()
        self.assertEqual(path.read_bytes(), before)

    def test_dialog_corrupt_cover_can_be_replaced_after_confirmation(self):
        path = self.make_cover()
        path.write_bytes(b"broken jpeg")
        dialog = self.dialog()
        dialog.pending_image = self.image
        dialog.reload_cover()
        self.assertTrue(dialog.save_button.isEnabled())
        with patch("opldeck.ui.game_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog.save()
        self.assertFalse(decode_image(image_bytes(path)).isNull())

    def test_dialog_read_only_while_busy_and_discard_before_action(self):
        dialog = self.dialog(editable=False)
        self.assertFalse(dialog.choose_button.isEnabled())
        self.assertFalse(dialog.root_button.isEnabled())
        self.assertFalse(dialog.save_button.isEnabled())
        dialog.pending_image = self.image
        with patch("opldeck.ui.game_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            dialog.finish_action("folder")
        self.assertIsNone(dialog.requested_action)
        with patch("opldeck.ui.game_gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard):
            dialog.finish_action("folder")
        self.assertEqual(dialog.requested_action, "folder")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_main_window_opens_ficha_and_dispatches_existing_action(self):
        window = MainWindow(auto_devices=False)
        self.addCleanup(window.close)
        path = make_iso(self.root / "Synthetic.iso")
        window.load_isos([str(path)])
        self.assertTrue(window.pool.waitForDone(5000))
        self.app.processEvents()
        self.assertIsNone(window.worker)
        window.iso_table.selectRow(0)
        observed = []

        def open_dialog(dialog):
            observed.append(dialog.game_id)
            dialog.requested_action = "convert"
            return QDialog.DialogCode.Accepted

        with patch.object(GameDialog, "exec", open_dialog), \
                patch("opldeck.ui.gui.InstallDialog.exec", return_value=QDialog.DialogCode.Rejected) as convert:
            window.show_details_dialog(window.iso_table)
        self.assertEqual(observed, [self.game_id])
        convert.assert_called_once()

    def test_loose_image_requires_explicit_library(self):
        source = make_iso(self.root / "Synthetic.iso")
        dialog = GameDialog(iso_rows([str(source)])[0], "ISO", "Dados técnicos")
        self.addCleanup(dialog.deleteLater)
        dialog.pending_image = self.image
        dialog.reload_cover()
        self.assertFalse(dialog.save_button.isEnabled())
        destination = self.root / "library"
        destination.mkdir()
        with patch("opldeck.ui.game_gui.QFileDialog.getExistingDirectory", return_value=str(destination)):
            dialog.choose_root()
        self.assertTrue(dialog.save_button.isEnabled())
        dialog.save()
        self.assertTrue((destination / "ART" / f"{self.game_id}_COV.png").exists())
        self.assertFalse((self.root / "ART").exists())

    def test_rename_from_ficha_preserves_cover_by_id(self):
        window = MainWindow(auto_devices=False)
        self.addCleanup(window.close)
        iso = make_iso(self.root / "Synthetic.iso")
        install_iso(iso, self.root, media=Media.DVD)
        cover = self.make_cover()
        before = cover.read_bytes()
        window.load_cfg(self.root / "ul.cfg")
        self.assertTrue(window.pool.waitForDone(5000))
        self.app.processEvents()
        window.ul_table.selectRow(0)

        def open_dialog(dialog):
            self.assertEqual(dialog.root, self.root)
            dialog.finish_action("rename")
            return dialog.result()

        with patch.object(GameDialog, "exec", open_dialog), \
                patch("opldeck.ui.gui.QInputDialog.getText", return_value=("New Title", True)):
            window.show_details_dialog(window.ul_table)
            self.assertTrue(window.pool.waitForDone(5000))
            self.app.processEvents()
            self.assertTrue(window.pool.waitForDone(5000))
            self.app.processEvents()
        self.assertIsNone(window.worker)
        self.assertEqual(read_cfg(self.root / "ul.cfg")[0].title, "New Title")
        self.assertEqual(cover.read_bytes(), before)
