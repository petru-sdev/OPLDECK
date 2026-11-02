"""ZIP/7z locais, preparação no PC e limpeza dos extraídos após verificação."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from opldeck.library.archives import ArchiveResult, detect_format, extract_archives
from opldeck.core.conversion import BUFFER_SIZE, CancelledError, _check_cancel, _regular_reader
from opldeck.core.formats import FormatError, validate_game_id
from opldeck.core.iso import inspect_iso
from opldeck.core.binimage import convert_bin, inspect_bin
from opldeck.core.batch import BatchProgress
from opldeck.core.ul import read_cfg

MANIFEST_LIMIT = 1024 * 1024


def _read_record(path):
    # Bound the actual read, not just a prior stat: a file can grow or be
    # replaced between those operations. Never trust the JSON's root type.
    with _regular_reader(path) as stream:
        before = os.fstat(stream.fileno())
        data = stream.read(MANIFEST_LIMIT + 1)
        after = os.fstat(stream.fileno())
    if len(data) > MANIFEST_LIMIT:
        raise ValueError('Registro de importação excede o limite.')
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('Registro de importação alterado durante a leitura.')
    try:
        record = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('Registro de importação contém JSON inválido.') from None
    if not isinstance(record, dict):
        raise ValueError('Registro de importação precisa ser um objeto JSON.')
    return record


def _record_schema(record):
    def identity(value, size):
        return (isinstance(value, list) and len(value) == size and
                all(type(n) is int for n in value) and all(n >= 0 for n in value[:min(size, 3)]))

    if (type(record.get('version')) is not int or record['version'] != 2
            or any(not isinstance(record.get(key), str) or not record[key]
                   for key in ('folder', 'manifest', 'archive', 'directory'))
            or not identity(record.get('folder_identity'), 2)
            or not identity(record.get('archive_snapshot'), 5)
            or any(type(record.get(key)) is not bool for key in ('safe_to_clean', 'complete'))):
        raise ValueError('Campos inválidos no registro de importação.')
    files, games, installed = record.get('files'), record.get('games'), record.get('installed')
    if (not isinstance(files, dict) or not files or any(not identity(value, 5) for value in files.values())
            or not isinstance(games, list) or any(not isinstance(name, str) for name in games)
            or len(set(games)) != len(games) or not set(games) <= set(files)
            or not isinstance(installed, dict) or not set(installed) <= set(games)):
        raise ValueError('Registro de arquivos extraídos inválido.')
    for value in installed.values():
        if (not isinstance(value, list) or len(value) != 2
                or not all(isinstance(item, str) for item in value) or not Path(value[0]).is_absolute()):
            raise ValueError('Destino inválido no registro de importação.')
        validate_game_id(value[1])

def workspace_root():
    override = os.environ.get('OPLDECK_IMPORT_CACHE')
    return Path(override) if override else Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'OPLDeck' / 'imports'


@dataclass(frozen=True)
class ImportPreferences:
    folder: str = ''
    enabled: bool = False
    cleanup: str = 'ask'

    @classmethod
    def load(cls, settings):
        folder = settings.value('import/folder', '')
        if not isinstance(folder, str) or (folder and not Path(folder).is_absolute()):
            return cls()
        def checked(key):
            value = settings.value(key, False)
            return value is True or isinstance(value, str) and value.lower() == 'true'
        cleanup = settings.value('import/cleanup', 'ask')
        return cls(folder, bool(folder) and checked('import/enabled'), cleanup if cleanup in ('always', 'never', 'ask') else 'ask')

    def values(self):
        if self.enabled:
            folder_identity(Path(self.folder))
        return {'import/folder': self.folder, 'import/enabled': self.enabled,
                'import/cleanup': self.cleanup}


def folder_identity(path):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError('Escolha uma pasta existente, sem links simbólicos no caminho.')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('A Pasta de import precisa ser uma pasta.')
    return (info.st_dev, info.st_ino)


def snapshot(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f'Arquivo regular esperado: {path}')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def unchanged(path, expected):
    if snapshot(path) != tuple(expected):
        raise ValueError(f'Arquivo alterado; preservado: {path}')


def save_record(record):
    path = Path(record['manifest'])
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def checked_record(path, folder):
    record = _read_record(path)
    _record_schema(record)
    if (record.get('version') != 2 or record.get('folder') != str(folder)
            or tuple(record.get('folder_identity', ())) != folder_identity(folder)
            or record.get('manifest') != str(path)):
        raise ValueError('Registro de outra pasta de importação.')
    archive = Path(record['archive'])
    if archive.parent != folder or '..' in archive.parts:
        raise ValueError('Origem inválida no registro de importação.')
    directory = Path(record['directory'])
    if directory.parent != path.parent or not directory.name.startswith('OPLDeck-extraidos-'):
        raise ValueError('Pasta de extração inválida no registro.')
    for name in record['files']:
        item = Path(name)
        if not item.is_relative_to(directory) or '..' in item.parts or item.suffix.lower() not in ('.iso', '.bin', '.cue'):
            raise ValueError('Arquivo fora da pasta de extração.')
    if (path.parent.parent != workspace_root() or not re.fullmatch(r'task-[a-zA-Z0-9_]+', path.parent.name)
            or not set(record['games']) <= set(record['files'])):
        raise ValueError('Registro fora da área temporária do aplicativo.')
    return record


def discover(folder):
    """Somente leitura, executada fora da interface; não percorre subpastas de jogos."""
    folder = Path(folder)
    identity = folder_identity(folder)
    records, errors = [], []
    state = workspace_root()
    if state.exists() or state.is_symlink():
        folder_identity(state)
        for directory in sorted(state.iterdir()):
            if not re.fullmatch(r'task-[a-zA-Z0-9_]+', directory.name):
                continue
            try:
                folder_identity(directory)
                for path in directory.glob('manifest-*.json'):
                    header = _read_record(path)
                    if header.get('folder') == str(folder):
                        records.append(checked_record(path, folder))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append(f'Importação preservada para revisão: {directory} · {exc}')
    known = {(r['archive'], tuple(r['archive_snapshot'])) for r in records}
    archives = {}
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.is_symlink() or path.name.endswith(('.part', '.crdownload', '.download')):
            continue
        if any(Path(str(path) + suffix).exists() for suffix in ('.part', '.crdownload', '.download')):
            continue
        try:
            detect_format(path)
            info = snapshot(path)
            if info[2] and (str(path), info) not in known:
                archives[str(path)] = info
        except (OSError, ValueError):
            continue
    # Corrupted bookkeeping must not cause automatic repeated extraction.
    return identity, {} if errors else archives, records, errors


@dataclass(frozen=True)
class FolderImportResult:
    archive_result: object
    record: dict


@dataclass(frozen=True)
class FolderImportBatch:
    archive_result: object
    records: tuple[dict, ...]


def prepare_imports(paths, preferences, *, plans=None, cancel=None, progress=None):
    state = workspace_root()
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    folder_identity(state)
    task = Path(tempfile.mkdtemp(prefix='task-', dir=state))
    try:
        return _prepare_imports(paths, task, plans=plans, cancel=cancel, progress=progress)
    except CancelledError:
        shutil.rmtree(task)
        raise


def _prepare_imports(paths, task, *, plans=None, cancel=None, progress=None):
    result = extract_archives(paths, task, plans=plans, cancel=cancel, progress=progress)
    records, games, errors = [], [], list(result.errors)
    for index, (archive, files) in enumerate(result.groups, 1):
        prepared, failures = [], []
        owned = list(files)
        for path in files:
            _check_cancel(cancel)
            try:
                if path.suffix.lower() == '.iso':
                    inspect_iso(path)
                    prepared.append(path)
                elif path.suffix.lower() == '.cue':
                    inspect_bin(path)
                    converted = path.parent / '.opldeck-convertidos'
                    converted.mkdir(exist_ok=True)
                    output = converted / (path.stem + '.iso')
                    # A normalização ainda pertence à preparação local. O total
                    # permanece em 50%, reservando a outra metade à transferência.
                    def report(event):
                        if progress:
                            progress(BatchProgress(index, len(result.groups), path.name,
                                                   'Preparação BIN/CUE', event,
                                                   sum(result.sizes), 2 * sum(result.sizes), event.phase))
                    convert_bin(path, output, cancel=cancel, progress=report)
                    owned.append(output)
                    prepared.append(output)
            except (OSError, ValueError) as exc:
                failures.append(f'{archive.name} / {path.name}: {exc}')
        folder = archive.parent
        record = {'version': 2, 'folder': str(folder), 'folder_identity': folder_identity(folder),
                  'archive': str(archive), 'archive_snapshot': snapshot(archive),
                  'directory': str(result.directory), 'manifest': str(task / f'manifest-{index:03}.json'),
                  'files': {str(path): snapshot(path) for path in owned},
                  'games': [str(path) for path in prepared], 'safe_to_clean': not failures,
                  'installed': {}, 'complete': False}
        save_record(record)
        records.append(record)
        games.extend(prepared)
        errors.extend(failures)
    if not games:
        raise FormatError('Nenhum jogo foi identificado. Arquivos preservados para revisão.\n' + '\n'.join(errors))
    identified = ArchiveResult(result.directory, tuple(games), tuple(p.stat().st_size for p in games),
                               tuple(errors), result.sources, result.groups)
    return FolderImportBatch(identified, tuple(records))


def prepare_archive(archive, expected, preferences, *, cancel=None, progress=None, plan=None):
    archive = Path(archive)
    if archive.parent != Path(preferences.folder):
        raise ValueError('Arquivo fora da Pasta de import.')
    unchanged(archive, expected)
    result = prepare_imports([archive], preferences, plans=[plan] if plan else None, cancel=cancel, progress=progress)
    return FolderImportResult(result.archive_result, result.records[0])


def digest(paths, cancel=None, guard=None):
    value = hashlib.sha256()
    for path in paths:
        before = snapshot(path)
        with _regular_reader(path) as stream:
            while True:
                _check_cancel(cancel)
                if guard:
                    guard()
                chunk = stream.read(BUFFER_SIZE)
                if not chunk:
                    break
                value.update(chunk)
        unchanged(path, before)
    return value.digest()


def verify_copy(source, destination, game_id, cancel=None, guard=None):
    cfg = destination / 'ul.cfg'
    cfg_before = snapshot(cfg)
    matches = [entry for entry in read_cfg(cfg) if entry.game_id == game_id]
    if len(matches) != 1:
        raise FormatError(f'Jogo ausente ou duplicado no destino: {game_id}')
    parts = [destination / matches[0].part_name(index) for index in range(matches[0].parts)]
    before = {path: snapshot(path) for path in parts}
    if sum(item[2] for item in before.values()) != source.stat().st_size:
        raise FormatError(f'Cópia com tamanho diferente; limpeza suspensa: {game_id}')
    if digest([source], cancel, guard) != digest(parts, cancel, guard):
        raise FormatError(f'Cópia com conteúdo diferente; limpeza suspensa: {game_id}')
    for path, expected in {cfg: cfg_before, **before}.items():
        unchanged(path, expected)
    return {cfg: cfg_before, **before}


@dataclass(frozen=True)
class FolderTransferResult:
    transfer: object
    removed: tuple[str, ...]
    warnings: tuple[str, ...]


def finish_imports(records, jobs, *, cancel=None, guard=None, delete_extracted=False):
    """Verifica o lote inteiro antes da primeira remoção; chamado no trabalhador."""
    removed, staged, eligible = [], [], []
    destinations = {}
    try:
        for original in records:
            related = [job for job in jobs if job.kind == 'install' and str(job.source) in original['files']]
            if not related or original['complete']:
                continue
            folder = Path(original['folder'])
            folder_identity(Path(original['manifest']).parent)
            record = checked_record(Path(original['manifest']), folder)
            if record['complete']:
                continue
            if not record.get('safe_to_clean', False):
                raise ValueError('Há falhas de identificação neste arquivo compactado; temporários preservados.')
            verified = set()
            for job in related:
                unchanged(job.source, record['files'][str(job.source)])
                destinations.update(verify_copy(job.source, job.destination, job.game_id, cancel, guard))
                record['installed'][str(job.source)] = [str(job.destination), job.game_id]
                verified.add(str(job.source))
            staged.append(record)
            if set(record['installed']) != set(record['games']):
                continue
            for name, expected in record['files'].items():
                path = Path(name)
                if path.resolve(strict=True) != path:
                    raise ValueError(f'Caminho de extração alterado: {path}')
                unchanged(path, expected)
                if name in record['games'] and name not in verified:
                    target, game_id = record['installed'][name]
                    destinations.update(verify_copy(path, Path(target), game_id, cancel, guard))
            unchanged(Path(record['archive']), record['archive_snapshot'])
            eligible.append(record)
        for path, expected in destinations.items():
            unchanged(path, expected)
        _check_cancel(cancel)
        if guard:
            guard()
        for record in staged:
            save_record(record)
        for record in eligible:
            _check_cancel(cancel)
            if guard:
                guard()
            if folder_identity(Path(record['folder'])) != tuple(record['folder_identity']):
                raise ValueError('A Pasta de import foi substituída; limpeza suspensa.')
            # Tombstone precedes deletion: a cleanup failure cannot reimport a ZIP.
            record['complete'] = True
            save_record(record)
            if not delete_extracted:
                continue
            for name, expected in record['files'].items():
                path = Path(name)
                if path.resolve(strict=True) != path:
                    raise ValueError(f'Caminho de extração alterado: {path}')
                unchanged(path, expected)
                path.unlink()
                removed.append(name)
            # Remove empty managed folders only; never erase unknown files.
            directory = Path(record['directory'])
            parents = {parent for name in record['files'] for parent in Path(name).parents
                       if parent == directory or parent.is_relative_to(directory)}
            for parent in sorted(parents, key=lambda p: len(p.parts), reverse=True):
                try:
                    parent.rmdir()
                except OSError:
                    pass
    except (OSError, ValueError, KeyError, TypeError, CancelledError) as exc:
        return tuple(removed), (f'Limpeza não concluída; confira os arquivos preservados: {exc}',)
    return tuple(removed), ()
