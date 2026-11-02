from pathlib import Path
import hashlib
import os
import shutil
import stat
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import zipfile

from opldeck.library.archives import extract_archives
from opldeck.core.conversion import CancelledError, export_ul, install_iso
from opldeck.core.formats import FormatError, Media, PART_SIZE
from tests.fixtures import SECTOR, dual, make_iso


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / 'out'
        self.destination.mkdir()
        self.iso = make_iso(self.root / 'Game.iso').read_bytes()

    def archive(self, name='games.zip', members=None, compression=zipfile.ZIP_DEFLATED):
        path = self.root / name
        with zipfile.ZipFile(path, 'w', compression=compression) as archive:
            for name, data in (members or [('DVD/Game.iso', self.iso)]):
                archive.writestr(name, data)
        return path

    def test_multiple_archives_preserve_folders_originals_and_existing_files(self):
        paths = [self.archive('one.zip'), self.archive('two.zip')]
        before = [path.read_bytes() for path in paths]
        other = self.destination / 'keep.iso'
        other.write_bytes(b'keep')
        events = []
        result = extract_archives(paths, self.destination, progress=events.append)
        self.assertEqual(len(result.paths), 2)
        self.assertNotEqual(result.paths[0], result.paths[1])
        self.assertTrue(all(path.parent.name == 'DVD' for path in result.paths))
        self.assertTrue(all(path.read_bytes() == self.iso for path in result.paths))
        self.assertEqual([path.read_bytes() for path in paths], before)
        self.assertEqual(other.read_bytes(), b'keep')
        self.assertEqual(events[-1].overall_completed * 2, events[-1].overall_total)
        self.assertEqual(events[-1].phase, 'complete')
        self.assertEqual([e.overall_completed for e in events], sorted(e.overall_completed for e in events))

    def test_only_isos_are_extracted(self):
        path = self.archive(members=[('cover.png', b'image'), ('folder/GAME.ISO', self.iso)])
        result = extract_archives([path], self.destination)
        self.assertEqual([p.suffix for p in result.paths], ['.ISO'])
        self.assertFalse(list(result.directory.rglob('*.png')))

    def test_standard_library_compression_methods(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            with self.subTest(compression=compression):
                archive = self.archive(compression=compression)
                result = extract_archives([archive], self.destination)
                self.assertEqual(result.paths[0].read_bytes(), self.iso)

    def test_unsafe_names_and_symlinks_rejected_before_writing(self):
        link = zipfile.ZipInfo('link.iso')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for name in ('../escape.iso', '/absolute.iso', 'a/../../escape.iso', 'C:/game.iso', 'a\\b.iso', link):
            with self.subTest(name=name):
                archive = self.archive(members=[(name, self.iso)])
                with self.assertRaises(FormatError):
                    extract_archives([archive], self.destination)
                self.assertEqual(list(self.destination.iterdir()), [])

    def test_duplicates_and_file_directory_conflicts_are_rejected(self):
        for names in [('Game.iso', 'GAME.ISO'), ('Game.iso', 'Game.iso/Other.iso')]:
            with self.subTest(names=names):
                archive = self.archive(members=[(name, self.iso) for name in names])
                with self.assertRaisesRegex(FormatError, 'conflitantes'):
                    extract_archives([archive], self.destination)
                self.assertEqual(list(self.destination.iterdir()), [])

    def test_crc_failure_preserves_successful_files_and_continues_batch(self):
        good = self.archive('good.zip')
        bad = self.archive('bad.zip', compression=zipfile.ZIP_STORED)
        with zipfile.ZipFile(bad) as archive:
            info = archive.infolist()[0]
            offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        data = bytearray(bad.read_bytes())
        data[offset + 100] ^= 1
        bad.write_bytes(data)
        result = extract_archives([bad, good], self.destination)
        self.assertEqual(len(result.paths), 1)
        self.assertEqual(result.paths[0].read_bytes(), self.iso)
        self.assertEqual(len(result.errors), 1)
        self.assertTrue(good.is_file())
        self.assertTrue(bad.is_file())

    def test_cancel_during_second_iso_removes_first_and_partial(self):
        archive = self.archive(members=[('One.iso', self.iso), ('Two.iso', self.iso)])
        cancel = Event()
        def report(event):
            if event.index == 2 and event.current.completed:
                cancel.set()
        with patch('opldeck.library.archives.BUFFER_SIZE', 1024), self.assertRaises(CancelledError):
            extract_archives([archive], self.destination, cancel=cancel, progress=report)
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertTrue(archive.is_file())

    def test_broken_deflate_stream_does_not_stop_next_archive(self):
        bad = self.archive('broken.zip')
        good = self.archive('good.zip')
        with zipfile.ZipFile(bad) as archive:
            info = archive.infolist()[0]
            offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        data = bytearray(bad.read_bytes())
        data[offset] = 7  # BTYPE=3 é reservado/inválido em DEFLATE.
        bad.write_bytes(data)
        result = extract_archives([bad, good], self.destination)
        self.assertEqual(len(result.errors), 1)
        self.assertIn('ZIP corrompidos', result.errors[0])
        self.assertEqual(len(result.paths), 1)
        self.assertEqual(result.paths[0].read_bytes(), self.iso)

    def test_changed_zip_is_not_published(self):
        archive = self.archive()
        changed = False
        def report(event):
            nonlocal changed
            if not changed and event.phase == 'complete':
                with archive.open('ab') as stream:
                    stream.write(b'changed')
                changed = True
        with self.assertRaisesRegex(FormatError, 'alterado'):
            extract_archives([archive], self.destination, progress=report)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_missing_space_leaves_destination_untouched(self):
        archive = self.archive()
        with patch('opldeck.library.archives._free_space', side_effect=OSError('Sem espaço')), self.assertRaises(OSError):
            extract_archives([archive], self.destination)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_password_flag_and_no_iso_are_rejected(self):
        archive = self.archive(compression=zipfile.ZIP_STORED)
        data = bytearray(archive.read_bytes())
        data[6] |= 1
        data[data.index(b'PK\x01\x02') + 8] |= 1
        archive.write_bytes(data)
        with self.assertRaisesRegex(FormatError, 'senha'):
            extract_archives([archive], self.destination)
        archive = self.archive(members=[('readme.txt', b'Nothing')])
        with self.assertRaisesRegex(FormatError, 'Nenhuma'):
            extract_archives([archive], self.destination)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_source_symlink_is_rejected(self):
        archive = self.archive()
        link = self.root / 'link.zip'
        link.symlink_to(archive)
        with self.assertRaises(OSError):
            extract_archives([link], self.destination)
        self.assertEqual(list(self.destination.iterdir()), [])


@unittest.skipUnless(os.environ.get('OPLDECK_LARGE_TESTS') == '1', 'Teste ZIP64 multi-GiB opt-in')
class LargeArchiveTests(unittest.TestCase):
    def test_zip64_extract_install_export_over_four_gib(self):
        workspace = Path(__file__).resolve().parents[2]
        size = 4 * PART_SIZE + SECTOR
        if shutil.disk_usage(workspace).free < size * 3 + (1 << 30):
            self.skipTest('Menos de 13 GiB livres para integração ZIP64')
        with tempfile.TemporaryDirectory(prefix='.opldeck-zip64-', dir=workspace) as directory:
            root = Path(directory)
            iso = make_iso(root / 'Large.iso')
            with iso.open('r+b') as stream:
                stream.truncate(size)
                stream.seek(16 * SECTOR + 80)
                stream.write(dual(size // SECTOR, 4))
                for boundary in range(PART_SIZE, size, PART_SIZE):
                    stream.seek(boundary - 16)
                    stream.write(bytes(range(32)))
                stream.seek(size - 16)
                stream.write(b'END-ZIP64-TEST!!')
            archive = root / 'Large.zip'
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as target:
                target.write(iso, 'DVD/Large.iso')
            with zipfile.ZipFile(archive) as source:
                self.assertGreater(source.infolist()[0].file_size, 0xffffffff)
            result = extract_archives([archive], root)
            self.assertEqual(result.paths[0].stat().st_size, size)
            destination = root / 'usb'
            destination.mkdir()
            entry = install_iso(result.paths[0], destination, media=Media.DVD)
            self.assertEqual(entry.parts, 5)
            output = export_ul(destination / 'ul.cfg', entry.game_id, root / 'out.iso')
            with iso.open('rb') as source, output.open('rb') as target:
                self.assertEqual(hashlib.file_digest(source, 'sha256').digest(),
                                 hashlib.file_digest(target, 'sha256').digest())
