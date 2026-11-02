from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from opldeck.library import recovery
from opldeck.core.conversion import CancelledError, TRANSACTION_DIR, directory_lock, export_ul, install_iso
from opldeck.core.formats import FormatError, Media
from opldeck.library.recovery import build_records, choices_from_plan, rebuild_ul, scan_ul
from opldeck.core.ul import ULEntry, read_cfg
from tests.fixtures import make_iso

ROOT = Path(__file__).resolve().parents[2]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.library = self.root / "usb"
        self.library.mkdir()
        self.iso = make_iso(self.root / "Synthetic.iso")
        self.entry = install_iso(self.iso, self.library, media=Media.DVD)
        self.cfg = self.library / "ul.cfg"

    def missing_index(self):
        self.cfg.unlink()
        return scan_ul(self.library)

    def choices(self, scan):
        return [{"key": g.key, "title": self.entry.title, "media": "DVD", "include": True} for g in scan.games]

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.library.iterdir() if p.is_file()}

    def test_preview_is_read_only_and_unknown_title_is_explicit(self):
        scan = self.missing_index()
        before = self.snapshot()
        game = scan.games[0]
        self.assertEqual((game.title, game.media), ("", ""))
        self.assertEqual(game.game_id, self.entry.game_id)
        self.assertEqual(game.crc, self.entry.crc)
        self.assertFalse(game.errors)
        with self.assertRaises(FormatError):
            build_records(scan, scan.to_plan()["games"])
        self.assertEqual(self.snapshot(), before)

    def test_missing_index_rebuilt_and_export_matches_original(self):
        scan = self.missing_index()
        before = self.snapshot()
        events = []
        result = rebuild_ul(scan, self.choices(scan), progress=events.append)
        self.assertEqual(read_cfg(self.cfg), [self.entry])
        self.assertEqual((result.games, result.excluded, result.backup), (1, 0, None))
        self.assertEqual(events[-1].total, 1)
        self.assertEqual({name: self.snapshot()[name] for name in before}, before)
        rebuilt = export_ul(self.cfg, self.entry.game_id, self.root / "out.iso")
        self.assertEqual(rebuilt.read_bytes(), self.iso.read_bytes())

    def test_title_crc_media_and_empty_selection_validation(self):
        scan = self.missing_index()
        for changes in [{"title": "Different"}, {"title": "Ação"}, {"title": "x" * 33},
                        {"title": ""}, {"media": ""}, {"media": "AUTO"}, {"include": "true"}]:
            with self.subTest(changes=changes), self.assertRaises(FormatError):
                rebuild_ul(scan, [{**self.choices(scan)[0], **changes}])
        with self.assertRaises(FormatError):
            rebuild_ul(scan, [])
        self.assertFalse(self.cfg.exists())

    def test_existing_index_requires_explicit_replacement_and_preserves_padding(self):
        original = bytearray(self.cfg.read_bytes())
        original[20] = 55
        original[49] = 33
        self.cfg.write_bytes(original)
        scan = scan_ul(self.library)
        self.assertEqual(scan.games[0].title, self.entry.title)
        self.assertEqual(scan.games[0].media, "DVD")
        with self.assertRaises(FileExistsError):
            rebuild_ul(scan, scan.to_plan()["games"])
        result = rebuild_ul(scan, scan.to_plan()["games"], replace_existing=True)
        self.assertEqual(self.cfg.read_bytes(), original)
        self.assertEqual(result.backup.read_bytes(), original)

    def test_corrupt_index_salvages_valid_records_and_backs_up_exact_bytes(self):
        original = self.entry.to_bytes() + b"bad" * 21 + b"!" + b"truncated"
        self.cfg.write_bytes(original)
        scan = scan_ul(self.library)
        self.assertEqual(len(scan.warnings), 2)
        self.assertEqual(scan.games[0].title, self.entry.title)
        result = rebuild_ul(scan, scan.to_plan()["games"], replace_existing=True)
        self.assertEqual(result.backup.read_bytes(), original)
        self.assertEqual(read_cfg(self.cfg), [self.entry])

    def test_old_record_without_parts_is_reported_before_replacement(self):
        other = ULEntry("Missing", "SLES_555.55", 1, Media.CD)
        self.cfg.write_bytes(self.entry.to_bytes() + other.to_bytes())
        scan = scan_ul(self.library)
        self.assertTrue(any("sem partes" in warning and "Missing" in warning for warning in scan.warnings))

    def test_no_space_keeps_index_and_parts(self):
        scan = scan_ul(self.library)
        before = self.snapshot()
        with patch("opldeck.library.recovery._free_space", side_effect=FormatError("Espaço insuficiente")):
            with self.assertRaisesRegex(FormatError, "Espaço"):
                rebuild_ul(scan, scan.to_plan()["games"], replace_existing=True)
        self.assertEqual(self.snapshot(), before)

    def test_destination_appearing_at_atomic_publish_is_not_overwritten(self):
        scan = self.missing_index()
        original_move = recovery._move

        def collide(source, destination):
            if destination == self.cfg:
                self.cfg.write_bytes(b"external index")
            original_move(source, destination)

        with patch("opldeck.library.recovery._move", side_effect=collide), self.assertRaises(FileExistsError):
            rebuild_ul(scan, self.choices(scan))
        self.assertEqual(self.cfg.read_bytes(), b"external index")

    def test_process_exit_before_and_after_publish(self):
        script = '''
import os, sys
from pathlib import Path
from unittest.mock import patch
from opldeck.library import recovery as r
root, phase = Path(sys.argv[1]), sys.argv[2]
scan = r.scan_ul(root)
choices = [{"key": scan.games[0].key, "include": True, "title": "Synthetic", "media": "DVD"}]
if phase == "before":
    r.rebuild_ul(scan, choices, replace_existing=True, progress=lambda _: os._exit(71))
else:
    original_sync = r._sync_directory
    def stop_after_publish(fd):
        if (root / "ul.cfg").stat().st_size == 64:
            os._exit(72)
        original_sync(fd)
    with patch.object(r, "_sync_directory", side_effect=stop_after_publish):
        r.rebuild_ul(scan, choices, replace_existing=True)
'''
        for phase, code in [("before", 71), ("after", 72)]:
            self.cfg.write_bytes(b"corrupt index")
            result = subprocess.run([sys.executable, "-c", script, str(self.library), phase],
                                    cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual((self.library / self.entry.part_name(0)).read_bytes(), self.iso.read_bytes())
            self.assertEqual(self.cfg.read_bytes(), b"corrupt index" if phase == "before" else self.entry.to_bytes())
        backups = list(self.library.glob("ul.cfg.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"corrupt index")

    def test_invalid_old_index_does_not_invent_title(self):
        self.cfg.write_bytes(b"broken")
        scan = scan_ul(self.library)
        self.assertFalse(scan.games[0].title)
        self.assertTrue(scan.warnings)
        result = rebuild_ul(scan, self.choices(scan), replace_existing=True)
        self.assertEqual(result.backup.read_bytes(), b"broken")

    def test_multislice_metadata_crosses_parts(self):
        self.cfg.unlink()
        (self.library / self.entry.part_name(0)).unlink()
        data = self.iso.read_bytes()
        part_size = 4096
        entry = replace(self.entry, parts=len(data) // part_size)
        for i in range(entry.parts):
            (self.library / entry.part_name(i)).write_bytes(data[i * part_size:(i + 1) * part_size])
        with patch("opldeck.library.recovery.PART_SIZE", part_size):
            scan = scan_ul(self.library)
            self.assertFalse(scan.games[0].errors)
            rebuild_ul(scan, self.choices(scan))
        self.assertEqual(read_cfg(self.cfg), [entry])

    def test_missing_middle_and_nonzero_start_block_recovery(self):
        scan = self.missing_index()
        first = self.library / self.entry.part_name(0)
        first.rename(first.with_suffix(".02"))
        scan = scan_ul(self.library)
        self.assertIn("Sequência", scan.games[0].errors[0])
        with self.assertRaises(FormatError):
            rebuild_ul(scan, self.choices(scan))
        self.assertFalse(self.cfg.exists())

    def test_truncated_image_and_wrong_internal_id_are_blocked(self):
        self.cfg.unlink()
        part = self.library / self.entry.part_name(0)
        original = part.read_bytes()
        part.write_bytes(original[:-2048])
        scan = scan_ul(self.library)
        self.assertTrue(any("truncad" in error for error in scan.games[0].errors))
        other = make_iso(self.root / "other.iso", game_id="SLES_555.55")
        part.write_bytes(other.read_bytes())
        scan = scan_ul(self.library)
        self.assertIn("ID interno", scan.games[0].errors[0])

    def test_noncanonical_and_duplicate_indices_are_blocked(self):
        self.cfg.unlink()
        part = self.library / self.entry.part_name(0)
        noncanonical = part.with_name(part.name.lower())
        part.rename(noncanonical)
        scan = scan_ul(self.library)
        self.assertIn("padrão", scan.games[0].errors[0])
        part.write_bytes(self.iso.read_bytes())
        scan = scan_ul(self.library)
        self.assertIn("duplicada", scan.games[0].errors[0])

    def test_symlink_part_and_cfg_are_not_followed(self):
        self.cfg.unlink()
        part = self.library / self.entry.part_name(0)
        part.unlink()
        part.symlink_to(self.iso)
        scan = scan_ul(self.library)
        self.assertTrue(any("links" in error for error in scan.games[0].errors))
        self.cfg.symlink_to(self.iso)
        with self.assertRaises(OSError):
            scan_ul(self.library)

    def test_malformed_name_is_reported_and_preserved(self):
        scan = self.missing_index()
        unknown = self.library / "ul.bad-name"
        unknown.write_bytes(b"preserve")
        scan = scan_ul(self.library)
        self.assertIn("não reconhecido", scan.warnings[0])
        rebuild_ul(scan, self.choices(scan))
        self.assertEqual(unknown.read_bytes(), b"preserve")

    def test_duplicate_ids_require_explicit_group_selection(self):
        self.cfg.unlink()
        other = replace(self.entry, title="Other Title")
        (self.library / other.part_name(0)).write_bytes(self.iso.read_bytes())
        scan = scan_ul(self.library)
        self.assertIn("mais de um grupo", scan.warnings[0])
        choices = [{"key": g.key, "include": True, "title": self.entry.title if g.crc == self.entry.crc else other.title,
                    "media": "DVD"} for g in scan.games]
        with self.assertRaisesRegex(FormatError, "duplicado"):
            rebuild_ul(scan, choices)
        choices[1]["include"] = False
        result = rebuild_ul(scan, choices)
        self.assertEqual((result.games, result.excluded), (1, 1))
        self.assertTrue((self.library / other.part_name(0)).exists())

    def test_changed_part_or_added_part_invalidates_preview(self):
        scan = self.missing_index()
        part = self.library / self.entry.part_name(0)
        part.write_bytes(self.iso.read_bytes())
        with self.assertRaisesRegex(FormatError, "alterada"):
            rebuild_ul(scan, self.choices(scan))
        scan = scan_ul(self.library)
        extra = self.library / "ul.unknown"
        extra.write_bytes(b"external")
        with self.assertRaisesRegex(FormatError, "alterada"):
            rebuild_ul(scan, self.choices(scan))
        self.assertFalse(self.cfg.exists())

    def test_external_index_change_during_progress_is_preserved(self):
        scan = self.missing_index()
        foreign = b"foreign"
        with self.assertRaisesRegex(FormatError, "alterada"):
            rebuild_ul(scan, self.choices(scan), progress=lambda _: self.cfg.write_bytes(foreign))
        self.assertEqual(self.cfg.read_bytes(), foreign)
        self.assertFalse(list(self.library.glob(".opldeck-rebuild-*")))

    def test_cancel_removes_temporary_index_and_keeps_parts(self):
        scan = self.missing_index()
        before = self.snapshot()
        cancel = Event()
        with self.assertRaises(CancelledError):
            rebuild_ul(scan, self.choices(scan), cancel=cancel, progress=lambda _: cancel.set())
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(list(self.library.iterdir())), 1)

    def test_modified_prepared_index_is_not_published(self):
        scan = self.missing_index()

        def change_prepared(_):
            temporary = next(self.library.glob(".opldeck-rebuild-*"))
            temporary.write_bytes(b"foreign trailing data" * 20)

        with self.assertRaisesRegex(FormatError, "preparado"):
            rebuild_ul(scan, self.choices(scan), progress=change_prepared)
        self.assertFalse(self.cfg.exists())

    def test_publish_failure_preserves_original_and_backup(self):
        scan = scan_ul(self.library)
        with patch("opldeck.library.recovery.os.replace", side_effect=OSError("publish failed")):
            with self.assertRaises(OSError) as caught:
                rebuild_ul(scan, scan.to_plan()["games"], replace_existing=True)
        self.assertEqual(self.cfg.read_bytes(), self.entry.to_bytes())
        backup = list(self.library.glob("ul.cfg.backup-*"))
        self.assertEqual(len(backup), 1)
        self.assertEqual(backup[0].read_bytes(), self.entry.to_bytes())
        self.assertIn("Backup preservado", caught.exception.__notes__[0])

    def test_fsync_failure_before_publication_leaves_no_index(self):
        scan = self.missing_index()
        with patch("opldeck.library.recovery.os.fsync", side_effect=OSError("fsync")):
            with self.assertRaises(OSError):
                rebuild_ul(scan, self.choices(scan))
        self.assertFalse(self.cfg.exists())
        self.assertFalse(list(self.library.glob(".opldeck-*")))

    def test_post_publish_sync_failure_keeps_new_index(self):
        scan = self.missing_index()
        with patch("opldeck.library.recovery._sync_directory", side_effect=OSError("sync failed")):
            with self.assertRaises(OSError) as caught:
                rebuild_ul(scan, self.choices(scan))
        self.assertIn("já foi publicado", caught.exception.__notes__[0])
        self.assertEqual(read_cfg(self.cfg), [self.entry])

    def test_lock_and_pending_management_block_recovery(self):
        with directory_lock(self.library):
            with self.assertRaises(FormatError):
                scan_ul(self.library)
        (self.library / TRANSACTION_DIR).mkdir()
        with self.assertRaisesRegex(FormatError, "pendente"):
            scan_ul(self.library)

    def test_stale_or_malformed_plan_rejected(self):
        scan = self.missing_index()
        plan = scan.to_plan()
        plan["games"] = self.choices(scan)
        self.assertEqual(choices_from_plan(scan, plan), self.choices(scan))
        for bad in [[], {**plan, "fingerprint": "wrong"}, {**plan, "games": "bad"},
                    {**plan, "games": [{**self.choices(scan)[0], "key": "../unsafe"}]}]:
            with self.assertRaises(FormatError):
                choices_from_plan(scan, bad)

    def test_cli_scan_plan_rebuild_and_confirmation(self):
        self.cfg.unlink()
        plan_path = self.root / "plan.json"

        def run(*args):
            return subprocess.run([sys.executable, "-m", "opldeck", *map(str, args)], cwd=ROOT, capture_output=True, text=True)

        result = run("scan-ul", self.library, "--output", plan_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(plan_path.read_text())
        plan["games"][0].update(title="Synthetic", media="DVD")
        plan_path.write_text(json.dumps(plan))
        rejected = run("rebuild-ul", self.library, "--plan", plan_path)
        self.assertEqual(rejected.returncode, 2)
        self.assertFalse(self.cfg.exists())
        result = run("rebuild-ul", self.library, "--plan", plan_path, "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["games"], 1)
        self.assertIn("registros", result.stderr)
        self.assertEqual(read_cfg(self.cfg), [self.entry])
