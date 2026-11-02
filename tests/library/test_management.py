import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from opldeck.library import management
from opldeck.core.conversion import CancelledError, TRANSACTION_DIR, directory_lock, export_ul, install_iso
from opldeck.core.formats import FormatError, Media
from opldeck.library.management import delete_ul, recover_ul, rename_ul
from opldeck.core.ul import ULEntry, read_cfg
from tests.fixtures import make_iso

ROOT = Path(__file__).resolve().parents[2]


class ManagementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.iso = make_iso(self.root / "Synthetic.iso")
        self.library = self.root / "usb"
        self.library.mkdir()
        self.entry = install_iso(self.iso, self.library, media=Media.DVD)
        self.cfg = self.library / "ul.cfg"
        self.journal = self.library / TRANSACTION_DIR

    def rename(self, title="Renamed", **kwargs):
        return rename_ul(self.cfg, self.entry.game_id, title, **kwargs)

    def delete(self, **kwargs):
        return delete_ul(self.cfg, self.entry.game_id, **kwargs)

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.library.iterdir() if p.is_file()}

    def assert_original(self):
        self.assertEqual(read_cfg(self.cfg), [self.entry])
        self.assertEqual((self.library / self.entry.part_name(0)).read_bytes(), self.iso.read_bytes())
        self.assertFalse(self.journal.exists())

    def test_rename_roundtrip_preserves_all_other_bytes(self):
        other = bytearray(ULEntry("Other", "SLES_555.55", 1, Media.CD).to_bytes())
        other[18] = 42
        original = bytearray(self.entry.to_bytes())
        original[20] = 33  # Padding não canônico do título antigo.
        original[49:64] = bytes(range(15))
        self.cfg.write_bytes(bytes(other + original + other))
        events = []
        renamed = self.rename(progress=events.append)
        self.assertNotEqual(renamed.crc, self.entry.crc)
        self.assertEqual(self.cfg.read_bytes(), bytes(other) + b"Renamed".ljust(32, b"\0") + bytes(original[32:]) + bytes(other))
        output = export_ul(self.cfg, renamed.game_id, self.root / "roundtrip.iso")
        self.assertEqual(output.read_bytes(), self.iso.read_bytes())
        self.assertFalse((self.library / self.entry.part_name(0)).exists())
        self.assertEqual(events[-1].completed, self.iso.stat().st_size)
        self.assertFalse(self.journal.exists())

    def test_delete_preserves_unrelated_files_and_records(self):
        other = ULEntry("Other", "SLES_555.55", 1, Media.CD)
        self.cfg.write_bytes(other.to_bytes() + self.entry.to_bytes())
        extra = self.library / other.part_name(0)
        extra.write_bytes(b"other data")
        self.delete()
        self.assertEqual(self.snapshot(), {"ul.cfg": other.to_bytes(), extra.name: b"other data"})
        self.assertEqual(self.iso.stat().st_size, 49152)

    def test_delete_last_entry_leaves_empty_index(self):
        self.delete()
        self.assertEqual(self.snapshot(), {"ul.cfg": b""})
        self.assertFalse(self.journal.exists())

    def test_same_title_is_noop(self):
        before = self.snapshot()
        self.assertEqual(self.rename(self.entry.title), self.entry)
        self.assertEqual(before, self.snapshot())

    def test_invalid_title_and_duplicate_title_id(self):
        before = self.snapshot()
        for title in ["", "Ação", "x" * 33, "bad\n"]:
            with self.assertRaises(FormatError):
                self.rename(title)
        self.assertEqual(before, self.snapshot())
        self.cfg.write_bytes(self.entry.to_bytes() + ULEntry("Other", "SLES_555.55", 1, Media.CD).to_bytes())
        with self.assertRaisesRegex(FormatError, "título"):
            self.rename("other")
        self.cfg.write_bytes(self.entry.to_bytes() * 2)
        with self.assertRaisesRegex(FormatError, "duplicado"):
            self.delete()

    def test_destination_collision_is_preserved(self):
        target = self.library / ULEntry("Renamed", self.entry.game_id, 1, Media.DVD).part_name(0)
        target.write_bytes(b"external")
        with self.assertRaises(FileExistsError):
            self.rename()
        self.assertEqual(target.read_bytes(), b"external")
        self.assert_original()

    def test_symlink_parts_are_still_rejected(self):
        part = self.library / self.entry.part_name(0)
        part.unlink()
        part.symlink_to(self.iso)
        with self.assertRaises(FormatError):
            self.delete()
        self.assertEqual(self.iso.stat().st_size, 49152)
        self.assertFalse(self.journal.exists())

    def test_symlink_cfg_is_rejected(self):
        self.cfg.unlink()
        external = self.root / "index"
        external.write_bytes(self.entry.to_bytes())
        self.cfg.symlink_to(external)
        with self.assertRaises(OSError):
            self.delete()
        self.assertEqual(external.read_bytes(), self.entry.to_bytes())

    def damaged(self):
        (self.library / self.entry.part_name(0)).unlink()
        self.entry = ULEntry('Synthetic', self.entry.game_id, 3, Media.DVD)
        self.cfg.write_bytes(self.entry.to_bytes())
        (self.library / self.entry.part_name(0)).write_bytes(b'truncated')
        # Parte 01 ausente, parte 02 vazia.
        (self.library / self.entry.part_name(2)).touch()

    def test_delete_damaged_game_removes_only_present_referenced_parts(self):
        self.damaged()
        other = ULEntry('Other', 'SLES_555.55', 1, Media.CD)
        self.cfg.write_bytes(self.entry.to_bytes() + other.to_bytes())
        (self.library / other.part_name(0)).write_bytes(b'keep')
        (self.library / 'ART').mkdir()
        (self.library / 'ART' / f'{self.entry.game_id}_COV.jpg').write_bytes(b'cover')
        extra = self.library / (self.entry.part_name(0)[:-2] + '03')
        extra.write_bytes(b'not referenced')
        entry, present, missing, total = management.management_summary(self.cfg, self.entry.game_id)
        self.assertEqual((len(present), len(missing), total), (2, 1, 9))
        self.assertEqual(entry, self.entry)
        self.delete()
        self.assertEqual(self.cfg.read_bytes(), other.to_bytes())
        self.assertEqual((self.library / other.part_name(0)).read_bytes(), b'keep')
        self.assertEqual(extra.read_bytes(), b'not referenced')
        self.assertTrue((self.library / 'ART' / f'{self.entry.game_id}_COV.jpg').exists())
        self.assertFalse((self.library / self.entry.part_name(0)).exists())
        self.assertFalse((self.library / self.entry.part_name(2)).exists())
        self.assertFalse(self.journal.exists())

    def test_rename_damaged_game_preserves_missing_and_invalid_parts(self):
        self.damaged()
        renamed = self.rename()
        self.assertEqual(read_cfg(self.cfg), [renamed])
        self.assertEqual((self.library / renamed.part_name(0)).read_bytes(), b'truncated')
        self.assertFalse((self.library / renamed.part_name(1)).exists())
        self.assertEqual((self.library / renamed.part_name(2)).stat().st_size, 0)
        with self.assertRaises((OSError, ValueError)):
            export_ul(self.cfg, renamed.game_id, self.root / 'invalid.iso')

    def test_all_parts_missing_can_rename_then_delete_record(self):
        (self.library / self.entry.part_name(0)).unlink()
        events = []
        renamed = self.rename(progress=events.append)
        self.assertEqual(read_cfg(self.cfg), [renamed])
        self.assertEqual(events[-1].total, 0)
        self.delete(progress=events.append)
        self.assertEqual(self.snapshot(), {'ul.cfg': b''})
        self.assertFalse(self.journal.exists())

    def test_empty_file_can_be_deleted(self):
        (self.library / self.entry.part_name(0)).write_bytes(b'')
        self.delete()
        self.assertEqual(self.snapshot(), {'ul.cfg': b''})

    def test_damaged_cancel_and_publish_failure_restore_exact_state(self):
        self.damaged()
        before = self.snapshot()
        for operation in (self.rename, self.delete):
            cancel = Event()
            with self.assertRaises(CancelledError):
                operation(cancel=cancel, progress=lambda _: cancel.set())
            self.assertEqual(self.snapshot(), before)
            with patch('opldeck.library.management.os.replace', side_effect=OSError('failed')), self.assertRaises(OSError):
                operation()
            self.assertEqual(self.snapshot(), before)
            self.assertFalse(self.journal.exists())

    def test_damaged_crash_before_publication_rolls_back_existing_parts(self):
        self.damaged()
        before = self.snapshot()
        for operation in ('rename', 'delete'):
            self.crash(operation, 'before')
            recover_ul(self.library)
            self.assertEqual(self.snapshot(), before)
            self.assertFalse(self.journal.exists())

    def test_damaged_crash_after_rename_preserves_absence(self):
        self.damaged()
        self.crash('rename', 'after')
        recover_ul(self.library)
        renamed = read_cfg(self.cfg)[0]
        self.assertEqual(renamed.title, 'Renamed')
        self.assertEqual((self.library / renamed.part_name(0)).read_bytes(), b'truncated')
        self.assertFalse((self.library / renamed.part_name(1)).exists())
        self.assertEqual((self.library / renamed.part_name(2)).stat().st_size, 0)

    def test_damaged_crash_after_delete_finishes_cleanup(self):
        self.damaged()
        self.crash('delete', 'after')
        recover_ul(self.library)
        self.assertEqual(self.snapshot(), {'ul.cfg': b''})
        self.assertFalse(self.journal.exists())

    def test_missing_part_reappearing_is_preserved_and_blocks_commit(self):
        self.damaged()
        absent = self.library / self.entry.part_name(1)
        def external_write(event):
            if event.part == 1:
                absent.write_bytes(b'foreign')
        with self.assertRaisesRegex(FormatError, 'reapareceu'):
            self.delete(progress=external_write)
        self.assertEqual(absent.read_bytes(), b'foreign')
        self.assertEqual(read_cfg(self.cfg), [self.entry])
        self.assertTrue(self.journal.exists())
        with self.assertRaisesRegex(FormatError, 'reapareceu'):
            recover_ul(self.library)

    def test_missing_part_target_collision_preserved(self):
        self.damaged()
        target = ULEntry('Renamed', self.entry.game_id, 3, Media.DVD)
        collision = self.library / target.part_name(1)
        collision.write_bytes(b'foreign')
        before = self.snapshot()
        with self.assertRaises(FileExistsError):
            self.rename()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.journal.exists())

    def test_v1_journal_remains_recoverable(self):
        self.crash('delete', 'before')
        manifest = self.journal / 'manifest.json'
        document = json.loads(manifest.read_text())
        document['version'] = 1
        manifest.write_text(json.dumps(document))
        recover_ul(self.library)
        self.assert_original()

    def test_v1_journal_cannot_claim_absent_parts(self):
        self.damaged()
        self.crash('delete', 'before')
        manifest = self.journal / 'manifest.json'
        document = json.loads(manifest.read_text())
        document['version'] = 1
        manifest.write_text(json.dumps(document))
        with self.assertRaisesRegex(FormatError, 'identidades inválidas'):
            recover_ul(self.library)
        self.assertTrue(self.journal.exists())

    def test_cancel_before_and_after_moving(self):
        for operation in [self.rename, self.delete]:
            with self.subTest(operation=operation):
                cancel = Event()
                cancel.set()
                with self.assertRaises(CancelledError):
                    operation(cancel=cancel)
                self.assert_original()
                cancel.clear()
                with self.assertRaises(CancelledError):
                    operation(cancel=cancel, progress=lambda _: cancel.set())
                self.assert_original()

    def test_multislice_cancel_restores_every_part(self):
        (self.library / self.entry.part_name(0)).unlink()
        entry = ULEntry("Synthetic", self.entry.game_id, 3, Media.DVD)
        self.cfg.write_bytes(entry.to_bytes())
        for i in range(3):
            (self.library / entry.part_name(i)).write_bytes(bytes([i]) * 4096)
        before = self.snapshot()
        cancel = Event()
        with self.assertRaises(CancelledError):
            self.delete(cancel=cancel, progress=lambda event: cancel.set() if event.part == 2 else None)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.journal.exists())

    def test_publish_failure_restores_parts(self):
        for operation in [self.rename, self.delete]:
            with patch("opldeck.library.management.os.replace", side_effect=OSError("publish failed")):
                with self.assertRaises(OSError):
                    operation()
            self.assert_original()

    def test_write_failure_before_journal_publication(self):
        with patch("opldeck.library.management._write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.delete()
        self.assert_original()
        self.assertEqual(len(list(self.library.iterdir())), 2)

    def test_sync_failure_before_publish_preserves_recoverable_state(self):
        with patch("opldeck.library.management._sync_directory", side_effect=OSError("fsync failed")):
            with self.assertRaises(OSError):
                self.delete()
        # Falha antes da publicação do diário: nenhuma parte foi movida.
        self.assert_original()

    def test_post_publish_sync_failure_retains_parts_until_recovery(self):
        original_sync = management._sync_directory

        def fail_after_publish(fd):
            if self.cfg.read_bytes() == b"":
                raise OSError("fsync after commit")
            original_sync(fd)

        with patch("opldeck.library.management._sync_directory", side_effect=fail_after_publish):
            with self.assertRaises(OSError) as caught:
                self.delete()
        self.assertIn("Recuperação pendente", caught.exception.__notes__[0])
        self.assertEqual((self.journal / "part-00").read_bytes(), self.iso.read_bytes())
        recover_ul(self.library)
        self.assertEqual(list(self.library.iterdir()), [self.cfg])

    def test_move_collision_after_preflight_does_not_overwrite(self):
        original_move = management._move
        target = self.library / ULEntry("Renamed", self.entry.game_id, 1, Media.DVD).part_name(0)

        def collide(source, destination):
            if destination == target:
                target.write_bytes(b"foreign")
            original_move(source, destination)

        with patch("opldeck.library.management._move", side_effect=collide), self.assertRaises(FileExistsError):
            self.rename()
        self.assertEqual(target.read_bytes(), b"foreign")
        self.assertEqual((self.library / self.entry.part_name(0)).read_bytes(), self.iso.read_bytes())
        self.assertEqual(read_cfg(self.cfg), [self.entry])
        self.assertTrue(self.journal.exists())

    def test_lock_blocks_management(self):
        with directory_lock(self.library):
            with self.assertRaisesRegex(FormatError, "Outra operação"):
                self.rename()
        self.assert_original()

    def test_pending_transaction_blocks_install_export_and_management(self):
        self.journal.mkdir()
        for operation in [self.rename, self.delete,
                          lambda: install_iso(self.iso, self.library, media=Media.DVD),
                          lambda: export_ul(self.cfg, self.entry.game_id, self.root / "out.iso")]:
            with self.assertRaisesRegex(FormatError, "pendente"):
                operation()
        recover_ul(self.library)
        self.assert_original()

    def crash(self, operation, phase):
        # Processo real: os._exit ignora finally/rollback e simula encerramento forçado.
        script = '''
import os, sys
from pathlib import Path
from unittest.mock import patch
from opldeck.library import management as m
cfg, operation, phase = sys.argv[1:]
function = m.rename_ul if operation == "rename" else m.delete_ul
args = (cfg, "SLUS_123.45", "Renamed") if operation == "rename" else (cfg, "SLUS_123.45")
if phase == "before":
    function(*args, progress=lambda _: os._exit(71))
elif phase == "after":
    with patch.object(m, "_recover_locked", side_effect=lambda _: os._exit(72)):
        function(*args)
elif phase == "cleanup":
    with patch.object(m, "_clean_journal", side_effect=lambda _: os._exit(73)):
        function(*args)
'''
        result = subprocess.run([sys.executable, "-c", script, str(self.cfg), operation, phase], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, {"before": 71, "after": 72, "cleanup": 73}[phase], result.stderr)
        self.assertTrue(self.journal.exists())

    def test_process_crash_before_publish_rolls_back(self):
        for operation in ["rename", "delete"]:
            self.crash(operation, "before")
            self.assertIn("desfeita", recover_ul(self.library))
            self.assert_original()
            self.assertIn("Nenhuma", recover_ul(self.library))

    def test_process_crash_after_rename_keeps_new_index(self):
        self.crash("rename", "after")
        self.assertIn("concluída", recover_ul(self.library))
        renamed = read_cfg(self.cfg)[0]
        self.assertEqual(renamed.title, "Renamed")
        self.assertEqual((self.library / renamed.part_name(0)).read_bytes(), self.iso.read_bytes())
        self.assertFalse(self.journal.exists())

    def test_process_crash_after_delete_finishes_cleanup(self):
        self.crash("delete", "after")
        self.assertEqual(self.cfg.read_bytes(), b"")
        self.assertTrue((self.journal / "part-00").exists())
        recover_ul(self.library)
        self.assertEqual(list(self.library.iterdir()), [self.cfg])

    def test_recovery_after_parts_already_deleted(self):
        self.crash("delete", "cleanup")
        self.assertFalse((self.journal / "part-00").exists())
        recover_ul(self.library)
        self.assertEqual(list(self.library.iterdir()), [self.cfg])

    def test_changed_cfg_preserved_and_recovery_refused(self):
        self.crash("rename", "before")
        external = ULEntry("External", "SLES_555.55", 1, Media.CD).to_bytes()
        self.cfg.write_bytes(external)
        with self.assertRaisesRegex(FormatError, "externamente"):
            recover_ul(self.library)
        self.assertEqual(self.cfg.read_bytes(), external)
        self.assertTrue(self.journal.exists())

    def test_changed_part_preserved_and_recovery_refused(self):
        self.crash("delete", "before")
        part = self.journal / "part-00"
        part.write_bytes(b"external data")
        with self.assertRaisesRegex(FormatError, "externamente"):
            recover_ul(self.library)
        self.assertEqual(part.read_bytes(), b"external data")
        self.assertTrue(self.journal.exists())

    def test_recovery_destination_collision_preserves_both(self):
        self.crash("delete", "before")
        original = self.library / self.entry.part_name(0)
        original.write_bytes(b"foreign")
        with self.assertRaisesRegex(FormatError, "ambíguo"):
            recover_ul(self.library)
        self.assertEqual(original.read_bytes(), b"foreign")
        self.assertEqual((self.journal / "part-00").read_bytes(), self.iso.read_bytes())

    def test_corrupt_manifest_and_unknown_files_preserved(self):
        self.crash("delete", "before")
        manifest = self.journal / "manifest.json"
        before = manifest.read_bytes()
        manifest.write_text('{"version": 42}')
        with self.assertRaisesRegex(FormatError, "inválido"):
            recover_ul(self.library)
        manifest.write_bytes(before)
        extra = self.journal / "foreign"
        extra.write_bytes(b"keep")
        with self.assertRaisesRegex(FormatError, "desconhecidos"):
            recover_ul(self.library)
        self.assertEqual(extra.read_bytes(), b"keep")

    def test_cleanup_failure_retains_recoverable_journal(self):
        original_unlink = Path.unlink

        def fail_quarantine(path, *args, **kwargs):
            if path.name == "part-00":
                raise OSError("unlink failed")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_quarantine), self.assertRaises(OSError) as caught:
            self.delete()
        self.assertIn("já foi publicada", caught.exception.__notes__[0])
        self.assertEqual(self.cfg.read_bytes(), b"")
        recover_ul(self.library)
        self.assertEqual(list(self.library.iterdir()), [self.cfg])

    def test_cli_confirmation_rename_delete_recovery(self):
        def run(*args):
            return subprocess.run([sys.executable, "-m", "opldeck", *map(str, args)], cwd=ROOT, capture_output=True, text=True)
        refused = run("delete-ul", self.cfg, self.entry.game_id)
        self.assertEqual(refused.returncode, 2)
        self.assert_original()
        renamed = run("rename-ul", self.cfg, self.entry.game_id.lower(), "Renamed")
        self.assertEqual(renamed.returncode, 0, renamed.stderr)
        self.assertEqual(json.loads(renamed.stdout)["title"], "Renamed")
        deleted = run("delete-ul", self.cfg, self.entry.game_id, "--yes")
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertEqual(self.cfg.read_bytes(), b"")
        recovered = run("recover-ul", self.library)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("Nenhuma", json.loads(recovered.stdout)["result"])
