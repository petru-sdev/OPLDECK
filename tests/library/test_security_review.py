"""Entradas adversas limitadas; sem abrir bombas reais na biblioteca vulnerável."""

import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import py7zr

from opldeck.library.archive_7z import library, open_7z
from opldeck.library.archives import extract_archives, list_archive
from opldeck.core.batch import TransferJob, run_batch
from opldeck.core.formats import FormatError, Media
from opldeck.library.import_folder import (
    ImportPreferences, checked_record, discover, finish_imports, prepare_archive, snapshot,
)
from opldeck.core.iso import read_game_id
from opldeck.core.ul import read_cfg
from tests.fixtures import make_iso

ROOT = Path(__file__).resolve().parents[2]


class SecurityReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / 'imports'
        self.folder.mkdir()
        self.output = self.root / 'output'
        self.output.mkdir()
        self.cache = self.root / 'cache'
        environment = patch.dict(os.environ, {'OPLDECK_IMPORT_CACHE': str(self.cache)})
        environment.start()
        self.addCleanup(environment.stop)

    def archive(self):
        iso = make_iso(self.root / 'Game.iso')
        source = self.folder / 'Game.zip'
        with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(iso, 'Game.iso')
        return source

    def prepared(self):
        source = self.archive()
        preferences = ImportPreferences(str(self.folder), True, 'always')
        return prepare_archive(source, snapshot(source), preferences)

    def test_vulnerable_unknown_and_prerelease_7z_are_blocked_before_parser(self):
        for version in ('0.20.2', '1.1.2', '1.1.3', '1.1.4rc1', '1.1.4.dev1', '', 'unknown', '1.2.0'):
            with self.subTest(version=version), patch.object(py7zr, '__version__', version), \
                    patch.object(py7zr, 'SevenZipFile') as parser:
                with self.assertRaisesRegex(FormatError, 'correções de segurança'):
                    open_7z(io.BytesIO(b'not sent to parser'), password='never-log-this')
                parser.assert_not_called()

    def test_safe_version_gate_passes_password_and_size_limit_to_parser(self):
        # Only exercise the gate with a mock; never mislabel the real 1.1.3
        # parser as patched or expose it to an adversarial header.
        for version in ('1.1.4', '1.1.4.post1', '1.1.5'):
            with self.subTest(version=version), patch.object(py7zr, '__version__', version), \
                    patch.object(py7zr, 'SevenZipFile') as parser:
                self.assertIs(open_7z(io.BytesIO(b''), password='secret', limit=123), parser.return_value)
                self.assertEqual(parser.call_args.kwargs, {'password': 'secret', 'max_extract_size': 123})

    def test_native_backend_cannot_bypass_unsafe_metadata_parser(self):
        source = self.folder / 'header.7z'
        source.write_bytes(b'7z\xbc\xaf\x27\x1c' + bytes(26))
        with patch.object(py7zr, '__version__', '1.1.3'), patch.object(py7zr, 'SevenZipFile') as parser:
            with self.assertRaisesRegex(FormatError, 'py7zr>=1.1.4'):
                list_archive(source, backend='system')
            parser.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_zip_continues_when_7z_is_blocked(self):
        source = self.archive()
        with patch.object(py7zr, '__version__', '1.1.3'):
            result = extract_archives([source], self.output)
        self.assertEqual(read_game_id(result.paths[0]), 'SLUS_123.45')
        self.assertTrue(source.exists())

    def test_zip_metadata_is_bounded_before_central_directory_allocation(self):
        path = self.folder / 'large-metadata.zip'
        size = 40 * 1024 * 1024
        with path.open('wb') as stream:
            stream.write(b'PK\x03\x04')
            stream.seek(size)
            stream.write(struct.pack('<4s4H2LH', b'PK\x05\x06', 0, 0, 1, 1, size, 0, 0))
        with self.assertRaisesRegex(FormatError, 'Metadados ZIP'):
            list_archive(path)
        # One malformed archive cannot discard a valid archive in the batch.
        result = extract_archives([path, self.archive()], self.output)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(read_game_id(result.paths[0]), 'SLUS_123.45')

    def test_zip_metadata_limit_does_not_limit_game_payload_reads(self):
        source = self.archive()
        with patch('opldeck.library.archives.MAX_ZIP_METADATA', 1024):
            result = extract_archives([source], self.output)
        self.assertEqual(result.paths[0].read_bytes(), (self.root / 'Game.iso').read_bytes())

    def test_public_readers_refuse_symlinks_and_preserve_target(self):
        source = make_iso(self.root / 'Game.iso')
        before = source.read_bytes()
        link = self.root / 'link.iso'
        link.symlink_to(source)
        for reader in (read_game_id, read_cfg):
            with self.subTest(reader=reader.__name__), self.assertRaises(OSError):
                reader(link)
        self.assertEqual(source.read_bytes(), before)

    def test_fifo_readers_and_cli_return_without_waiting_for_a_writer(self):
        fifo = self.root / 'input'
        os.mkfifo(fifo)
        commands = [
            [sys.executable, '-c', 'from opldeck.core.iso import read_game_id; import sys; read_game_id(sys.argv[1])', str(fifo)],
            [sys.executable, '-m', 'opldeck', 'list-ul', str(fifo)],
            [sys.executable, '-m', 'opldeck', 'rebuild-ul', str(self.output), '--plan', str(fifo), '--yes'],
        ]
        for command in commands:
            with self.subTest(command=command[1:3]):
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('arquivo regular', result.stderr)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_non_object_and_deep_json_are_reported_without_stopping_discovery(self):
        task = self.cache / 'task-test'
        task.mkdir(parents=True)
        path = task / 'manifest-001.json'
        for data in ('[]', 'null', 'true', '"string"', '[' * 2000 + '0' + ']' * 2000):
            with self.subTest(data=data[:20]):
                path.write_text(data)
                _, candidates, records, errors = discover(self.folder)
                self.assertFalse(candidates or records)
                self.assertTrue(errors)
                self.assertEqual(path.read_text(), data)

    def test_malformed_schema_never_resumes_or_deletes_files(self):
        result = self.prepared()
        path = Path(result.record['manifest'])
        original = path.read_text()
        cases = {'complete': 'false', 'safe_to_clean': 'false', 'games': [dict()],
                 'installed': [], 'archive_snapshot': [[], 0, 0, 0, 0],
                 'folder_identity': [True, 1], 'files': {str(result.archive_result.paths[0]): [1]}}
        for key, value in cases.items():
            with self.subTest(key=key):
                data = json.loads(original)
                data[key] = value
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    checked_record(path, self.folder)
                self.assertTrue(discover(self.folder)[3])
                self.assertTrue(all(item.exists() for item in result.archive_result.paths))

    def test_invalid_cleanup_flag_preserves_verified_game_and_original_archive(self):
        result = self.prepared()
        source = result.archive_result.paths[0]
        job = TransferJob('install', source, self.output, 'SLUS_123.45', 'Game', source.stat().st_size, Media.DVD)
        self.assertFalse(run_batch([job]).error)
        path = Path(result.record['manifest'])
        record = json.loads(path.read_text())
        record['safe_to_clean'] = 'false'
        path.write_text(json.dumps(record))
        removed, warnings = finish_imports([result.record], [job], delete_extracted=True)
        self.assertFalse(removed)
        self.assertTrue(warnings)
        self.assertTrue(source.exists())
        self.assertTrue(Path(record['archive']).exists())

    def test_manifest_read_has_a_hard_byte_limit_even_if_stat_is_stale(self):
        result = self.prepared()
        path = Path(result.record['manifest'])
        # It was valid at preparation; the actual read must enforce its own cap.
        path.write_bytes(b' ' * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, 'excede o limite'):
            checked_record(path, self.folder)
        self.assertTrue(discover(self.folder)[3])
        self.assertTrue(result.archive_result.paths[0].exists())
