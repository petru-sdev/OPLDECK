import random
import shutil
import string
import subprocess
import tempfile
import unittest
from pathlib import Path

from opldeck.core.crc import usba_crc32
from opldeck.core.formats import FormatError, Media, PART_SIZE, part_count
from opldeck.core.iso import inspect_iso, parse_system_cnf, read_game_id
from opldeck.core.ul import ULEntry, parse_cfg, read_cfg, serialize_cfg
from tests.fixtures import SECTOR, dual, make_iso

ROOT = Path(__file__).resolve().parents[2]


class CRCTests(unittest.TestCase):
    def test_c_reference(self):
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("Compilador C indisponível")
        rng = random.Random(23)
        names = ["", "A", "Gran Turismo 4", "a" * 32, "123456789"]
        names += ["".join(rng.choices(string.ascii_letters + string.digits + " -_", k=rng.randrange(33))) for _ in range(100)]
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "crc-reference"
            subprocess.run([compiler, "-std=c99", "-Wall", "-Wextra", "-Werror", "-o", str(executable),
                            str(ROOT / "tools/reference/crc_reference.c")], check=True, capture_output=True)
            result = subprocess.run([str(executable), *names], check=True, capture_output=True, text=True)
        self.assertEqual([f"{usba_crc32(name):08X}" for name in names], result.stdout.splitlines())

    def test_invalid_crc_input(self):
        for title in ["Ação", "x" * 33, "ABC\0DEF"]:
            with self.subTest(title=title), self.assertRaises(FormatError):
                usba_crc32(title)

    def test_nul_is_included(self):
        self.assertEqual(usba_crc32(""), 0)
        self.assertNotEqual(usba_crc32("A"), 0x30476DC0)  # CRC of A without its NUL


class ULTests(unittest.TestCase):
    def setUp(self):
        self.entry = ULEntry("Example Game", "SLUS_123.45", 3, Media.DVD)

    def test_record_layout(self):
        data = self.entry.to_bytes()
        self.assertEqual(len(data), 64)
        self.assertEqual(data[:32], b"Example Game" + bytes(20))
        self.assertEqual(data[32:47], b"ul.SLUS_123.45\0")
        self.assertEqual(data[47:49], b"\x03\x14")
        self.assertEqual(data[49:], bytes(4) + b"\x08" + bytes(10))

    def test_empty_multiple_and_full_length_titles(self):
        other = ULEntry("X" * 32, "SLES_555.55", 16, Media.CD)
        self.assertEqual(parse_cfg(b""), [])
        self.assertEqual(parse_cfg(serialize_cfg([self.entry, other])), [self.entry, other])
        self.assertEqual(other.to_bytes()[:32], b"X" * 32)
        self.assertTrue(other.part_name(15).endswith(".0f"))
        with self.assertRaises(FormatError):
            other.part_name(16)

    def test_reserved_bytes_survive(self):
        raw = bytearray(self.entry.to_bytes())
        raw[49:64] = bytes(range(15))
        self.assertEqual(serialize_cfg(parse_cfg(raw)), raw)

    def test_rejects_corruption(self):
        original = self.entry.to_bytes()
        cases = [original[:-1], original + b"x"]
        for offset, value in [(32, 0), (47, 0), (48, 0), (0, 255), (35, ord("/"))]:
            raw = bytearray(original)
            raw[offset] = value
            cases.append(bytes(raw))
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(FormatError):
                parse_cfg(raw)

    def test_invalid_fields(self):
        for title in ["", "x" * 33, "á", "a\nb"]:
            with self.assertRaises(FormatError):
                ULEntry(title, "SLUS_123.45", 1, Media.CD)

    def test_part_boundaries(self):
        for size, expected in [(1, 1), (PART_SIZE, 1), (PART_SIZE + 1, 2),
                               (PART_SIZE * 2, 2), (PART_SIZE * 5 // 2, 3),
                               (PART_SIZE * 4 + 1, 5), (255 * PART_SIZE, 255)]:
            self.assertEqual(part_count(size), expected)
        for size in [0, -1, 255 * PART_SIZE + 1]:
            with self.assertRaises(FormatError):
                part_count(size)

    def test_read_does_not_modify_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ul.cfg"
            raw = self.entry.to_bytes()
            path.write_bytes(raw)
            self.assertEqual(read_cfg(path), [self.entry])
            self.assertEqual(path.read_bytes(), raw)


class ISOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_system_cnf_variations(self):
        for text in [b"BOOT2 = cdrom0:\\SLUS_123.45;1\r\nVER = 1.00",
                     b'boot2= "cdrom0:\\sles_123.45;1"\n',
                     b"BOOT2\t=\tcdrom0:/SLUS_123.45;1"]:
            self.assertIn(parse_system_cnf(text), ["SLUS_123.45", "SLES_123.45"])
        for text in [b"BOOT = cdrom0:\\SLUS_123.45;1", b"# BOOT2 = cdrom0:\\SLUS_123.45;1",
                     b"BOOT2 = cdrom0:\\BAD;1", b""]:
            with self.assertRaises(FormatError):
                parse_system_cnf(text)

    def test_naruto_size_sparse_volume_above_signed_32_bit(self):
        path = make_iso(self.root / 'Naruto.iso', 'SLUS_217.27')
        size, sectors = 4_045_733_888, 1_975_456
        self.assertEqual(sectors * SECTOR, size)
        self.assertGreater(size, 2**31 - 1)
        with path.open('r+b') as stream:
            stream.truncate(size)
            stream.seek(16 * SECTOR + 80)
            stream.write(dual(sectors, 4))
        game = inspect_iso(path)
        self.assertEqual((game.game_id, game.size, game.parts), ('SLUS_217.27', size, 4))
        with path.open('r+b') as stream:
            stream.truncate(size - SECTOR)
        with self.assertRaisesRegex(FormatError, r'Volume=4045733888.*1975456.*arquivo=4045731840.*diferença=2048'):
            inspect_iso(path)

    def test_read_iso_and_sector_padding(self):
        for second_sector in [False, True]:
            for boot_record in [False, True]:
                with self.subTest(second_sector=second_sector, boot_record=boot_record):
                    path = make_iso(self.root / "Jogo.iso", second_sector=second_sector, boot_record=boot_record)
                    before = path.read_bytes()
                    self.assertEqual(read_game_id(path), "SLUS_123.45")
                    self.assertEqual(path.read_bytes(), before)

    def test_legacy_name_and_internal_id_mismatch(self):
        path = make_iso(self.root / "SLES_999.99.Teste.ISO")
        game = inspect_iso(path)
        self.assertEqual(game.title, "Teste")
        self.assertEqual(game.game_id, "SLUS_123.45")
        self.assertTrue(game.warning)
        self.assertTrue(game.media_estimated)
        self.assertEqual(game.media, Media.CD)

    def test_folder_media_takes_precedence(self):
        directory = self.root / "DVD"
        directory.mkdir()
        game = inspect_iso(make_iso(directory / "Teste.iso"))
        self.assertEqual(game.media, Media.DVD)
        self.assertFalse(game.media_estimated)

    def test_malformed_images(self):
        path = make_iso(self.root / "Teste.iso")
        original = path.read_bytes()
        for offset, value in [(16 * SECTOR + 1, 0), (16 * SECTOR + 130, 1),
                              (16 * SECTOR + 84, 1), (16 * SECTOR + 156, 10),
                              (20 * SECTOR, 2)]:
            with self.subTest(offset=offset):
                raw = bytearray(original)
                raw[offset] = value
                path.write_bytes(raw)
                with self.assertRaises(FormatError):
                    read_game_id(path)
        for length in [0, 20, 16 * SECTOR, 22 * SECTOR]:
            path.write_bytes(original[:length])
            with self.assertRaises(FormatError):
                read_game_id(path)

    def test_missing_system_cnf(self):
        path = make_iso(self.root / "Teste.iso")
        path.write_bytes(path.read_bytes().replace(b"SYSTEM.CNF;1", b"NOTSYS.CNF;1"))
        with self.assertRaisesRegex(FormatError, "não encontrado"):
            read_game_id(path)

    def test_unsupported_extension(self):
        with self.assertRaises(FormatError):
            inspect_iso(self.root / "image.zso")

    def test_cli_success_and_failure(self):
        path = make_iso(self.root / "Teste.iso")
        import sys
        result = subprocess.run([sys.executable, "-m", "opldeck", "inspect", str(path)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"game_id": "SLUS_123.45"', result.stdout)
        result = subprocess.run([sys.executable, "-m", "opldeck", "inspect", str(self.root / "missing.iso")],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Erro:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
