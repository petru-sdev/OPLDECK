import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from opldeck.core.conversion import CancelledError, directory_lock, export_ul, install_iso
from opldeck.core.formats import FormatError, Media, PART_SIZE
from opldeck.core.ul import ULEntry, read_cfg
from tests.fixtures import SECTOR, dual, make_iso

ROOT = Path(__file__).resolve().parents[2]


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.destination = self.root / "usb"
        self.destination.mkdir()
        self.iso = make_iso(self.root / "Synthetic.iso")

    def install(self, **kwargs):
        return install_iso(self.iso, self.destination, media=Media.DVD, **kwargs)

    def assert_empty(self):
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_roundtrip_and_progress(self):
        events = []
        original = self.iso.read_bytes()
        entry = self.install(progress=events.append, buffer_size=4096)
        self.assertEqual(read_cfg(self.destination / "ul.cfg"), [entry])
        self.assertEqual((self.destination / entry.part_name(0)).read_bytes(), original)
        result = export_ul(self.destination / "ul.cfg", entry.game_id, self.root / "rebuilt.iso")
        self.assertEqual(result.read_bytes(), original)
        self.assertEqual(self.iso.read_bytes(), original)
        self.assertEqual(events[-1].completed, len(original))
        self.assertEqual([e.completed for e in events], sorted(e.completed for e in events))

    def test_sync_phase_precedes_fsync_and_completion(self):
        events = []
        sync_seen = []
        real_sync = os.fsync
        def sync(descriptor):
            sync_seen.append(events[-1].phase if events else None)
            return real_sync(descriptor)
        with patch('opldeck.core.conversion.os.fsync', side_effect=sync):
            entry = self.install(progress=events.append)
        self.assertTrue(sync_seen)
        self.assertEqual(events[-1].phase, 'sync')
        self.assertTrue(all(phase == 'sync' for phase in sync_seen))
        events.clear()
        sync_seen.clear()
        with patch('opldeck.core.conversion.os.fsync', side_effect=sync):
            export_ul(self.destination / 'ul.cfg', entry.game_id, self.root / 'synced.iso', progress=events.append)
        self.assertEqual(events[-1].phase, 'sync')
        self.assertTrue(all(phase == 'sync' for phase in sync_seen))

    def test_multislice_and_exact_boundary(self):
        # Small boundaries exercise the same copy/rollback paths without multi-GiB I/O.
        for chunk_size, count in [(16384, 3), (20000, 3)]:
            with self.subTest(chunk_size=chunk_size):
                with patch("opldeck.core.conversion.PART_SIZE", chunk_size), patch("opldeck.core.formats.PART_SIZE", chunk_size):
                    entry = self.install()
                    self.assertEqual(entry.parts, count)
                    output = export_ul(self.destination / "ul.cfg", entry.game_id, self.root / "rebuilt.iso")
                    self.assertEqual(output.read_bytes(), self.iso.read_bytes())
                    output.unlink()
                    sizes = [(self.destination / entry.part_name(i)).stat().st_size for i in range(count)]
                    self.assertEqual(sizes[:-1], [chunk_size] * (count - 1))
                for path in self.destination.iterdir():
                    path.unlink()

    def test_requires_confirmed_media_and_valid_title(self):
        with self.assertRaisesRegex(FormatError, "Confirme"):
            install_iso(self.iso, self.destination)
        for title in ["Ação", "x" * 33, ""]:
            with self.assertRaises(FormatError):
                self.install(title=title)
        self.assert_empty()

    def test_duplicate_id_and_title(self):
        self.install()
        before = {p.name: p.read_bytes() for p in self.destination.iterdir()}
        with self.assertRaisesRegex(FormatError, "título"):
            self.install(title="synthetic")
        with self.assertRaisesRegex(FormatError, "ID"):
            self.install(title="Another title")
        self.assertEqual({p.name: p.read_bytes() for p in self.destination.iterdir()}, before)

    def test_preserves_existing_cfg_bytes(self):
        first = bytearray(ULEntry("Existing", "SLES_555.55", 1, Media.CD).to_bytes())
        first[20] = 42  # Noncanonical title padding must not be reserialized.
        first[49] = 127
        (self.destination / "ul.cfg").write_bytes(first)
        self.install()
        self.assertEqual((self.destination / "ul.cfg").read_bytes()[:64], first)

    def test_existing_part_and_output_not_overwritten(self):
        entry = ULEntry("Synthetic", "SLUS_123.45", 1, Media.DVD)
        part = self.destination / entry.part_name(0)
        part.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(part.read_bytes(), b"existing")
        part.unlink()
        self.install()
        with self.assertRaises(FileExistsError):
            export_ul(self.destination / "ul.cfg", entry.game_id, self.iso)
        self.assertEqual(self.iso.stat().st_size, 24 * SECTOR)

    def test_rejects_symlink_cfg_and_parts(self):
        external = self.root / "external.cfg"
        external.write_bytes(b"")
        cfg = self.destination / "ul.cfg"
        cfg.symlink_to(external)
        with self.assertRaises(OSError):
            self.install()
        self.assertEqual(external.read_bytes(), b"")
        cfg.unlink()
        entry = self.install()
        part = self.destination / entry.part_name(0)
        part.unlink()
        part.symlink_to(self.iso)
        with self.assertRaises(OSError):
            export_ul(cfg, entry.game_id, self.root / "out.iso")
        self.assertFalse((self.root / "out.iso").exists())

    def test_cancel_before_and_during_copy(self):
        cancel = Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            self.install(cancel=cancel)
        self.assert_empty()
        cancel.clear()
        with self.assertRaises(CancelledError):
            self.install(cancel=cancel, progress=lambda _: cancel.set(), buffer_size=4096)
        self.assert_empty()

    def test_cancel_after_last_chunk_keeps_index_unchanged(self):
        previous = ULEntry("Existing", "SLES_555.55", 1, Media.CD).to_bytes()
        cfg = self.destination / "ul.cfg"
        cfg.write_bytes(previous)
        cancel = Event()
        with self.assertRaises(CancelledError):
            self.install(cancel=cancel, progress=lambda e: cancel.set() if e.completed == e.total else None)
        self.assertEqual(list(self.destination.iterdir()), [cfg])
        self.assertEqual(cfg.read_bytes(), previous)

    def test_cancel_export_removes_partial(self):
        entry = self.install()
        cancel = Event()
        target = self.root / "out.iso"
        with self.assertRaises(CancelledError):
            export_ul(self.destination / "ul.cfg", entry.game_id, target,
                      cancel=cancel, progress=lambda _: cancel.set())
        self.assertFalse(target.exists())
        self.assertTrue((self.destination / entry.part_name(0)).exists())

    def test_no_space(self):
        usage = shutil.disk_usage(self.destination)
        with patch("opldeck.core.conversion.shutil.disk_usage", return_value=usage._replace(free=1)):
            with self.assertRaisesRegex(FormatError, "Espaço"):
                self.install()
        self.assert_empty()

    def test_sync_failure_and_publish_failure_roll_back(self):
        for operation in ["os.fsync", "os.replace"]:
            with self.subTest(operation=operation), patch("opldeck.core.conversion." + operation, side_effect=OSError("I/O failure")):
                with self.assertRaises(OSError):
                    self.install()
                self.assert_empty()

    def test_write_failure_rolls_back(self):
        def fail_copy(source, target, *args):
            target.write(b"partial")
            raise OSError("No space left on device")

        with patch("opldeck.core.conversion._copy", side_effect=fail_copy):
            with self.assertRaises(OSError):
                self.install()
        self.assert_empty()

    def test_committed_files_survive_post_publish_sync_error(self):
        with patch("opldeck.core.conversion._sync_directory", side_effect=[None, OSError("directory sync")]):
            with self.assertRaises(OSError) as caught:
                self.install()
        entry = read_cfg(self.destination / "ul.cfg")[0]
        self.assertEqual((self.destination / entry.part_name(0)).read_bytes(), self.iso.read_bytes())
        self.assertIn("já foi publicado", caught.exception.__notes__[0])

    def test_cleanup_does_not_remove_replaced_file(self):
        cancel = Event()
        part = self.destination / ULEntry("Synthetic", "SLUS_123.45", 1, Media.DVD).part_name(0)

        def replace_created(event):
            replacement = self.root / "foreign"
            replacement.write_bytes(b"foreign file")
            os.replace(replacement, part)
            cancel.set()

        with self.assertRaises(CancelledError):
            self.install(cancel=cancel, progress=replace_created)
        self.assertEqual(part.read_bytes(), b"foreign file")
        self.assertFalse((self.destination / "ul.cfg").exists())

    def test_changed_source_rolls_back(self):
        changed = False

        def modify(event):
            nonlocal changed
            if not changed:
                changed = True
                with self.iso.open("ab") as stream:
                    stream.write(b"change")

        with self.assertRaisesRegex(FormatError, "alterada"):
            self.install(progress=modify)
        self.assert_empty()

    def test_external_cfg_change_is_preserved(self):
        external_record = ULEntry("External", "SLES_555.55", 1, Media.CD).to_bytes()
        cfg = self.destination / "ul.cfg"

        def change_cfg(event):
            cfg.write_bytes(external_record)

        with self.assertRaisesRegex(FormatError, "outro programa"):
            self.install(progress=change_cfg)
        self.assertEqual(list(self.destination.iterdir()), [cfg])
        self.assertEqual(cfg.read_bytes(), external_record)

    def test_lock_refuses_concurrent_install(self):
        with directory_lock(self.destination):
            with self.assertRaisesRegex(FormatError, "Outra operação"):
                self.install()
        self.assert_empty()

    def test_missing_invalid_parts_and_unknown_id(self):
        entry = self.install()
        cfg = self.destination / "ul.cfg"
        output = self.root / "out.iso"
        with self.assertRaises(FormatError):
            export_ul(cfg, "SLES_999.99", output)
        part = self.destination / entry.part_name(0)
        part.write_bytes(b"")
        with self.assertRaises(FormatError):
            export_ul(cfg, entry.game_id, output)
        part.unlink()
        with self.assertRaises(FileNotFoundError):
            export_ul(cfg, entry.game_id, output)
        self.assertFalse(output.exists())

    def test_cli_install_export(self):
        result = subprocess.run([sys.executable, "-m", "opldeck", "install", str(self.iso),
                                 str(self.destination), "--media", "DVD"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"game_id": "SLUS_123.45"', result.stdout)
        output = self.root / "out.iso"
        result = subprocess.run([sys.executable, "-m", "opldeck", "export", str(self.destination / "ul.cfg"),
                                 "SLUS_123.45", str(output)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), self.iso.read_bytes())


@unittest.skipUnless(os.environ.get("OPLDECK_LARGE_TESTS") == "1", "Teste multi-GiB opt-in")
class LargeConversionTests(unittest.TestCase):
    def test_real_gib_boundaries(self):
        # Workspace storage, never /tmp (which may be a small tmpfs).
        sizes = [PART_SIZE, PART_SIZE * 5 // 2, PART_SIZE * 4 + SECTOR]
        if shutil.disk_usage(ROOT).free < 2 * max(sizes) + (1 << 30):
            self.skipTest("Menos de 10 GiB livres para integração")
        for size in sizes:
            with self.subTest(size=size), tempfile.TemporaryDirectory(prefix=".opldeck-integration-", dir=ROOT) as directory:
                root = Path(directory)
                iso = make_iso(root / "Synthetic.iso")
                with iso.open("r+b") as stream:
                    stream.truncate(size)
                    stream.seek(16 * SECTOR + 80)
                    stream.write(dual(size // SECTOR, 4))
                    for boundary in range(PART_SIZE, size, PART_SIZE):
                        stream.seek(boundary - 16)
                        stream.write(bytes(range(32)))
                    stream.seek(size - 16)
                    stream.write(b"END-OF-SYNTHETIC")
                destination = root / "usb"
                destination.mkdir()
                entry = install_iso(iso, destination, media=Media.DVD)
                expected = (size + PART_SIZE - 1) // PART_SIZE
                self.assertEqual(entry.parts, expected)
                self.assertEqual([(destination / entry.part_name(i)).stat().st_size for i in range(expected)],
                                 [PART_SIZE] * (expected - 1) + [size - PART_SIZE * (expected - 1)])
                output = export_ul(destination / "ul.cfg", entry.game_id, root / "out.iso")
                with iso.open("rb") as source, output.open("rb") as target:
                    self.assertEqual(hashlib.file_digest(source, "sha256").digest(),
                                     hashlib.file_digest(target, "sha256").digest())
