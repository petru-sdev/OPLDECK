import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from opldeck.core.binimage import RawISOReader, convert_bin, inspect_bin
from opldeck.core.conversion import CancelledError, directory_lock
from opldeck.core.formats import FormatError
from opldeck.core.iso import read_game_id
from tests.fixtures import make_bin, make_iso

ROOT = Path(__file__).resolve().parents[2]


class BINTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.iso = make_iso(self.root / "reference.iso", second_sector=True, boot_record=True)
        # Data outside ISO filesystem metadata tests that every sector is preserved.
        raw = bytearray(self.iso.read_bytes())
        raw[:2048] = bytes(range(256)) * 8
        raw[23 * 2048:] = bytes(reversed(range(256))) * 8
        self.iso.write_bytes(raw)
        self.bin = make_bin(self.iso, self.root / "Synthetic.bin")
        self.output = self.root / "output.iso"

    def cue(self, body=None):
        path = self.root / "Synthetic.cue"
        path.write_text(body or 'FILE "Synthetic.bin" BINARY\n TRACK 01 MODE1/2352\n INDEX 01 00:00:00\n', encoding="utf-8")
        return path

    def mutate(self, sector, offset, data):
        with self.bin.open("r+b") as stream:
            stream.seek(sector * 2352 + offset)
            stream.write(data)

    def test_mode1_mode2_roundtrip_and_metadata(self):
        for mode, offset in [(1, 16), (2, 24)]:
            with self.subTest(mode=mode):
                make_bin(self.iso, self.bin, mode)
                original = self.bin.read_bytes()
                game = inspect_bin(self.bin)
                self.assertEqual(game.game_id, "SLUS_123.45")
                self.assertEqual(game.mode, mode)
                self.assertEqual(game.payload_offset, offset)
                self.assertEqual(game.sectors, 24)
                self.assertEqual(game.raw_size, 24 * 2352)
                self.assertEqual(game.iso_size, self.iso.stat().st_size)
                events = []
                result = convert_bin(self.bin, self.output, buffer_size=4096, progress=events.append)
                self.assertEqual(result.read_bytes(), self.iso.read_bytes())
                self.assertEqual(hashlib.sha256(result.read_bytes()).digest(), hashlib.sha256(self.iso.read_bytes()).digest())
                self.assertEqual(read_game_id(result), game.game_id)
                self.assertEqual(self.bin.read_bytes(), original)
                self.assertEqual(len([event for event in events if event.phase == 'transfer']), 24)
                self.assertEqual(events[-1].phase, 'sync')
                self.assertEqual(events[-1].completed, game.iso_size)
                self.assertEqual([event.completed for event in events if event.phase == 'transfer'], list(range(2048, 24 * 2048 + 1, 2048)))
                self.output.unlink()

    def test_cue_single_track(self):
        for mode in [1, 2]:
            with self.subTest(mode=mode):
                make_bin(self.iso, self.bin, mode)
                cue = self.cue(f'\ufeffREM COMMENT synthetic\nTITLE "Sample"\nFILE "Synthetic.bin" BINARY\n'
                               f' TRACK 01 MODE{mode}/2352\n INDEX 01 00:00:00\n')
                self.assertEqual(inspect_bin(cue).bin_path, self.bin)
                convert_bin(cue, self.output)
                self.assertEqual(self.output.read_bytes(), self.iso.read_bytes())
                self.output.unlink()

    def test_cue_relative_subfolder_and_spaces(self):
        folder = self.root / "tracks"
        folder.mkdir()
        destination = folder / "Sample disc.bin"
        self.bin.rename(destination)
        cue = self.cue('FILE "tracks\\Sample disc.bin" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00')
        self.assertEqual(inspect_bin(cue).bin_path, destination)

    def test_rejects_non_ps2_boot(self):
        self.iso.write_bytes(self.iso.read_bytes().replace(b"BOOT2", b"BOOT "))
        make_bin(self.iso, self.bin)
        with self.assertRaisesRegex(FormatError, "BOOT2"):
            convert_bin(self.bin, self.output)
        self.assertFalse(self.output.exists())

    def test_empty_truncated_and_unsupported_extension(self):
        for raw in [b"", b"x", self.bin.read_bytes()[:-1]]:
            self.bin.write_bytes(raw)
            with self.assertRaisesRegex(FormatError, "2352"):
                inspect_bin(self.bin)
        with self.assertRaisesRegex(FormatError, "raw"):
            inspect_bin(self.iso)

    def test_initial_sector_validation(self):
        for offset, data, message in [(0, b"X", "sincronismo"), (15, b"\x00", "modo 0"),
                                       (20, b"\x01", "subcabeçalho"), (18, b"\x28", "subcabeçalho")]:
            with self.subTest(offset=offset):
                make_bin(self.iso, self.bin, mode=2)
                self.mutate(0, offset, data)
                with self.assertRaisesRegex(FormatError, message):
                    inspect_bin(self.bin)

    def test_late_sector_failure_removes_partial_and_reports_offset(self):
        # Sector 23 is not read during identification; failure must be caught during conversion.
        for mode, offset, data, message in [(1, 0, b"X", "sincronismo"), (1, 15, b"\x02", "mudança"),
                                           (2, 20, b"\x01", "subcabeçalho"),
                                           (2, 16, b"\x00\x00\x28\x00" * 2, "Form 2")]:
            with self.subTest(message=message):
                make_bin(self.iso, self.bin, mode)
                self.mutate(23, offset, data)
                self.assertEqual(inspect_bin(self.bin).game_id, "SLUS_123.45")
                with self.assertRaisesRegex(FormatError, message) as caught:
                    convert_bin(self.bin, self.output, buffer_size=4096)
                self.assertIn("Setor raw 23 (offset 0xD350)", str(caught.exception))
                self.assertFalse(self.output.exists())

    def test_cue_rejects_unsupported_layouts(self):
        valid = 'FILE "Synthetic.bin" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00'
        cases = [valid + '\nTRACK 02 AUDIO\nINDEX 01 00:01:00',
                 valid.replace("MODE1/2352", "AUDIO"), valid.replace("MODE1/2352", "MODE1/2048"),
                 valid.replace("00:00:00", "00:02:00"), valid.replace("INDEX 01", "INDEX 00"),
                 valid + "\nPREGAP 00:02:00", valid + "\nFLAGS DCP", valid + '\nFILE "other.bin" BINARY',
                 'FILE "Synthetic.bin" BINARY', 'TRACK 01 MODE1/2352\n' + valid,
                 valid.replace("BINARY", "WAVE"), valid + "\nINDEX 01 00:00:00"]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(FormatError):
                inspect_bin(self.cue(body))

    def test_cue_mode_mismatch_and_missing_bin(self):
        with self.assertRaisesRegex(FormatError, "mudança"):
            inspect_bin(self.cue('FILE "Synthetic.bin" BINARY\nTRACK 01 MODE2/2352\nINDEX 01 00:00:00'))
        self.bin.unlink()
        with self.assertRaises(FileNotFoundError):
            inspect_bin(self.cue())

    def test_cue_rejects_external_paths_and_oversize(self):
        for filename in ["../outside.bin", str(self.bin), "C:\\game.bin", "bad\0.bin"]:
            with self.subTest(filename=filename), self.assertRaisesRegex(FormatError, "pasta"):
                inspect_bin(self.cue(f'FILE "{filename}" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00'))
        cue = self.cue("REM " + "x" * (64 * 1024))
        with self.assertRaisesRegex(FormatError, "64 KiB"):
            inspect_bin(cue)
        cue.write_bytes(b"\xff")
        with self.assertRaisesRegex(FormatError, "UTF-8"):
            inspect_bin(cue)

    def test_reader_cross_sector_seek_and_eof(self):
        raw = self.bin.read_bytes()
        reader = RawISOReader(io.BytesIO(raw), len(raw), 1)
        expected = self.iso.read_bytes()
        reader.seek(2040)
        self.assertEqual(reader.read(32), expected[2040:2072])
        reader.seek(-16, 2)
        self.assertEqual(reader.read(50), expected[-16:])
        reader.seek(5, 1)
        self.assertEqual(reader.read(1), b"")
        self.assertEqual(reader.tell(), len(expected) + 5)
        with self.assertRaises(ValueError):
            reader.seek(-1)
        with self.assertRaises(ValueError):
            reader.seek(0, 5)

    def test_cancel_before_during_and_after_last_buffer(self):
        event = Event()
        event.set()
        with self.assertRaises(CancelledError):
            convert_bin(self.bin, self.output, cancel=event)
        for last_only in [False, True]:
            event.clear()

            def report(progress):
                if not last_only or progress.completed == progress.total:
                    event.set()

            with self.assertRaises(CancelledError):
                convert_bin(self.bin, self.output, cancel=event, progress=report, buffer_size=4096)
            self.assertFalse(self.output.exists())

    def test_existing_file_and_symlink_not_replaced(self):
        self.output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            convert_bin(self.bin, self.output)
        self.assertEqual(self.output.read_bytes(), b"existing")
        self.output.unlink()
        self.output.symlink_to(self.iso)
        with self.assertRaises(FileExistsError):
            convert_bin(self.bin, self.output)
        self.assertTrue(self.output.is_symlink())

    def test_invalid_destination_and_buffer(self):
        with self.assertRaises(FormatError):
            convert_bin(self.bin, self.root / "output.bin")
        with self.assertRaises(FormatError):
            convert_bin(self.bin, self.output, buffer_size=1)
        self.assertFalse(self.output.exists())

    def test_no_space_and_lock(self):
        usage = shutil.disk_usage(self.root)
        with patch("opldeck.core.conversion.shutil.disk_usage", return_value=usage._replace(free=0)):
            with self.assertRaisesRegex(FormatError, "Espaço"):
                convert_bin(self.bin, self.output)
        with directory_lock(self.root):
            with self.assertRaisesRegex(FormatError, "Outra operação"):
                convert_bin(self.bin, self.output)
        self.assertFalse(self.output.exists())

    def test_sync_failure_removes_partial(self):
        with patch("opldeck.core.binimage.os.fsync", side_effect=OSError("I/O error")):
            with self.assertRaises(OSError):
                convert_bin(self.bin, self.output)
        self.assertFalse(self.output.exists())

    def test_default_buffer_boundaries(self):
        size = 8 * 1024 * 1024 + 2048
        with self.iso.open("r+b") as stream:
            stream.truncate(size)
            for position in [(4 * 1024 * 1024 // 2352) * 2048 - 16, size - 32]:
                stream.seek(position)
                stream.write(bytes(range(32)))
        make_bin(self.iso, self.bin, mode=2)
        events = []
        convert_bin(self.bin, self.output, progress=events.append)
        self.assertGreater(len(events), 1)
        self.assertEqual(events[-1].completed, size)
        with self.iso.open("rb") as original, self.output.open("rb") as result:
            self.assertEqual(hashlib.file_digest(original, "sha256").digest(),
                             hashlib.file_digest(result, "sha256").digest())

    def test_write_failure_removes_partial(self):
        real_open = Path.open

        class FailingWriter:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def fileno(self):
                return self.stream.fileno()

            def write(self, data):
                self.stream.write(data[:64])
                raise OSError("Simulated full disk during write")

        def open_file(path, mode="r", *args, **kwargs):
            stream = real_open(path, mode, *args, **kwargs)
            return FailingWriter(stream) if path == self.output and mode == "xb" else stream

        with patch.object(Path, "open", open_file):
            with self.assertRaisesRegex(OSError, "full disk"):
                convert_bin(self.bin, self.output)
        self.assertFalse(self.output.exists())

    def test_source_truncation_and_modification(self):
        for truncate in [True, False]:
            make_bin(self.iso, self.bin)
            changed = False

            def modify(progress):
                nonlocal changed
                if not changed:
                    changed = True
                    with self.bin.open("r+b") as stream:
                        if truncate:
                            stream.truncate(24)
                        else:
                            stream.seek(16)
                            stream.write(b"changed")

            with self.assertRaises(FormatError):
                convert_bin(self.bin, self.output, progress=modify, buffer_size=4096)
            self.assertFalse(self.output.exists())

    def test_cli_inspection_and_conversion(self):
        result = subprocess.run([sys.executable, "-m", "opldeck", "inspect-bin", str(self.bin)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(result.stdout)
        self.assertEqual(metadata["payload_offset"], 16)
        result = subprocess.run([sys.executable, "-m", "opldeck", "bin2iso", str(self.cue()), str(self.output)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), self.iso.read_bytes())
        self.assertIn("setores 24/24", result.stderr)
        self.assertEqual(json.loads(result.stdout)["iso"], str(self.output))
