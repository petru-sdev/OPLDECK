import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import zipfile

import py7zr

from opldeck.library.archive_7z import library
from opldeck.library.archives import (ArchiveLimits, PasswordRequired, WrongPassword, detect_format,
                              extract_archives, list_archive)
from opldeck.core.conversion import CancelledError
from opldeck.core.formats import FormatError
from opldeck.library.import_folder import ImportPreferences, prepare_imports
from tests.fixtures import make_iso, make_bin

try:
    library()
    SAFE_7Z = True
except FormatError:
    SAFE_7Z = False

requires_safe_7z = unittest.skipUnless(SAFE_7Z, 'Integração 7z exige py7zr>=1.1.4,<1.2 instalado')


class Archive7zTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.out = self.root / 'out'
        self.out.mkdir()
        self.iso = make_iso(self.root / 'Game.iso')
        self.payload = self.iso.read_bytes()
        self.native = shutil.which('7zz') or shutil.which('7z')

    def seven(self, name='games.data', members=None, password=None, header=False, mutate=None):
        path = self.root / name
        with py7zr.SevenZipFile(path, 'w', password=password, header_encryption=header) as archive:
            for filename, data in members or [('DVD/Game.iso', self.payload), ('readme.html', b'ignored')]:
                archive.writestr(data, filename)
            if mutate:
                mutate(archive.header.files_info.files)
        return path

    def zip(self, members=None, password=None):
        path = self.root / 'archive.unrelated'
        if password:
            if not self.native:
                self.skipTest('7z do sistema necessário apenas para criar a fixture ZIP criptografada')
            subprocess.run([self.native, 'a', '-tzip', '-mem=ZipCrypto', '-p' + password, str(path), str(self.iso)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in members or [('Game.iso', self.payload)]:
                    archive.writestr(name, data)
        return path

    @requires_safe_7z
    def test_signature_detection_and_native_python_results_match(self):
        source = self.seven()
        before = source.read_bytes()
        self.assertEqual(detect_format(source), '7z')
        self.assertEqual(detect_format(self.zip()), 'zip')
        for backend in ['py7zr'] + (['system'] if self.native else []):
            plan = list_archive(source, backend=backend)
            self.assertEqual(plan.selected, ('DVD/Game.iso',))
            self.assertEqual(len(plan.members), 2)
            result = extract_archives([source], self.out, plans=[plan])
            self.assertEqual(result.paths[0].read_bytes(), self.payload)
            self.assertFalse(list(result.directory.rglob('*.html')))
        self.assertEqual(source.read_bytes(), before)

    @requires_safe_7z
    def test_system_missing_uses_py7zr(self):
        with patch('opldeck.library.archive_7z.shutil.which', return_value=None):
            plan = list_archive(self.seven())
            self.assertEqual(plan.backend, 'py7zr')
            result = extract_archives([plan.source], self.out, plans=[plan])
        self.assertEqual(result.paths[0].read_bytes(), self.payload)

    @requires_safe_7z
    def test_seven_paths_and_device_symlink_types_refused_before_writing(self):
        for name in ['../evil.iso', '/evil.iso', 'a/../../evil.iso', 'C:/evil.iso']:
            def mutate(files):
                files[0]['filename'] = name
            source = self.seven(mutate=mutate)
            with self.subTest(name=name), self.assertRaises(FormatError):
                extract_archives([source], self.out)
            self.assertEqual(list(self.out.iterdir()), [])
        for mode in (stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK):
            source = self.seven(mutate=lambda files: files[0].update(attributes=((mode | 0o644) << 16) | 0x8000))
            with self.subTest(mode=mode), self.assertRaises(FormatError):
                list_archive(source)
        self.assertEqual(list(self.out.iterdir()), [])

    @requires_safe_7z
    def test_limits_count_size_ratio_and_disk_space(self):
        for source in (self.zip(), self.seven()):
            for limits in (ArchiveLimits(files=0), ArchiveLimits(total_bytes=1024), ArchiveLimits(ratio=1)):
                with self.subTest(source=source, limits=limits), self.assertRaises(FormatError):
                    list_archive(source, limits=limits)
            with patch('opldeck.library.archives._free_space', side_effect=OSError('Disco cheio')), self.assertRaises(OSError):
                extract_archives([source], self.out)
            self.assertEqual(list(self.out.iterdir()), [])

    @requires_safe_7z
    def test_encrypted_seven_headers_and_wrong_password(self):
        for encrypted_header in (False, True):
            source = self.seven(password='fixture-password', header=encrypted_header)
            with self.assertRaises(PasswordRequired):
                list_archive(source)
            with self.assertRaises(WrongPassword):
                plan = list_archive(source, password='wrong', backend='py7zr')
                extract_archives([source], self.out, plans=[plan])
            self.assertEqual(list(self.out.iterdir()), [])
            plan = list_archive(source, password='fixture-password')
            self.assertEqual(plan.backend, 'py7zr')
            self.assertNotIn('fixture-password', repr(plan))
            result = extract_archives([source], self.out, plans=[plan])
            self.assertEqual(result.paths[0].read_bytes(), self.payload)
            shutil.rmtree(result.directory)

    def test_encrypted_zip_standard_library_and_wrong_password(self):
        source = self.zip(password='fixture-password')
        with self.assertRaises(PasswordRequired):
            list_archive(source)
        wrong = list_archive(source, password='wrong')
        with self.assertRaises(WrongPassword):
            extract_archives([source], self.out, plans=[wrong])
        self.assertEqual(list(self.out.iterdir()), [])
        plan = list_archive(source, password='fixture-password')
        result = extract_archives([source], self.out, plans=[plan])
        self.assertEqual(result.paths[0].read_bytes(), self.payload)

    @requires_safe_7z
    def test_corrupted_seven_payload_rejected_and_original_preserved(self):
        source = self.seven()
        data = bytearray(source.read_bytes())
        data[40] ^= 0x80
        source.write_bytes(data)
        for backend in ['py7zr'] + (['system'] if self.native else []):
            with self.subTest(backend=backend), self.assertRaises(FormatError):
                plan = list_archive(source, backend=backend)
                extract_archives([source], self.out, plans=[plan])
            self.assertEqual(list(self.out.iterdir()), [])
        self.assertEqual(source.read_bytes(), data)

    @requires_safe_7z
    def test_cancel_during_extraction_removes_owned_partials_for_both_backends(self):
        source = self.seven()
        for backend in ['py7zr'] + (['system'] if self.native else []):
            cancel = Event()
            plan = list_archive(source, backend=backend)
            def report(event):
                if event.current.completed:
                    cancel.set()
            with self.subTest(backend=backend), self.assertRaises(CancelledError):
                extract_archives([source], self.out, plans=[plan], cancel=cancel, progress=report)
            self.assertEqual(list(self.out.iterdir()), [])
        self.assertTrue(source.exists())

    @requires_safe_7z
    def test_mid_write_disk_full_cleans_partials(self):
        source = self.seven()
        from opldeck.library.archives import BoundedWriter
        with patch.object(BoundedWriter, 'write', side_effect=OSError(28, 'Disco cheio')), self.assertRaises(OSError):
            extract_archives([source], self.out)
        self.assertEqual(list(self.out.iterdir()), [])

    @requires_safe_7z
    def test_bin_cue_pair_selected_and_normalized_to_identified_iso(self):
        binary = make_bin(self.iso, self.root / 'Game.bin', 1)
        cue = b'FILE "Game.bin" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n'
        members = [('Game.cue', cue), ('Game.bin', binary.read_bytes()), ('orphan.bin', b'ignored'), ('readme.txt', b'ignored')]
        for source in (self.zip(members=members), self.seven(members=members)):
            plan = list_archive(source)
            self.assertEqual(set(plan.selected), {'Game.bin', 'Game.cue'})
            with patch.dict(os.environ, {'OPLDECK_IMPORT_CACHE': str(self.root / 'cache')}):
                result = prepare_imports([source], ImportPreferences(), plans=[plan])
            self.assertEqual(result.archive_result.paths[0].read_bytes(), self.payload)
            self.assertEqual(len(result.records[0]['files']), 3)
            self.assertEqual(len(result.records[0]['games']), 1)
            self.assertTrue(source.exists())

    @requires_safe_7z
    def test_selection_preserved_and_existing_outputs_never_overwritten(self):
        source = self.seven(members=[('one.iso', self.payload), ('two.iso', self.payload)])
        plan = list_archive(source).select(['two.iso'])
        existing = self.out / 'two.iso'
        existing.write_bytes(b'keep')
        result = extract_archives([source], self.out, plans=[plan])
        self.assertEqual([path.name for path in result.paths], ['two.iso'])
        self.assertEqual(existing.read_bytes(), b'keep')

    @requires_safe_7z
    def test_cancel_during_bin_normalization_cleans_complete_extraction_and_partial_iso(self):
        binary = make_bin(self.iso, self.root / 'Game.bin', 1)
        cue = b'FILE "Game.bin" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n'
        source = self.seven(members=[('Game.cue', cue), ('Game.bin', binary.read_bytes())])
        cancel = Event()
        events = []
        def report(event):
            events.append(event)
            if event.operation == 'Preparação BIN/CUE' and event.current.completed:
                cancel.set()
        cache = self.root / 'cache'
        with patch.dict(os.environ, {'OPLDECK_IMPORT_CACHE': str(cache)}), self.assertRaises(CancelledError):
            prepare_imports([source], ImportPreferences(), cancel=cancel, progress=report)
        self.assertEqual(list(cache.iterdir()), [])
        self.assertTrue(source.exists())
        preparation = [event for event in events if event.operation == 'Preparação BIN/CUE']
        self.assertTrue(preparation)
        self.assertTrue(all(event.overall_completed * 2 == event.overall_total for event in preparation))

    def test_multivolume_and_unsupported_formats_have_specific_errors(self):
        for name in ('game.7z.001', 'game.z01', 'game.zip.001'):
            path = self.root / name
            path.write_bytes(b'7z\xbc\xaf\x27\x1c')
            with self.assertRaisesRegex(FormatError, 'várias partes'):
                list_archive(path)
        path = self.root / 'fake.zip'
        path.write_bytes(b'Rar!\x1a\x07\x00')
        with self.assertRaisesRegex(FormatError, 'RAR, TAR'):
            list_archive(path)
