"""Importação de ZIP/7z com prévia, limites e extração exclusiva em área local."""

from dataclasses import dataclass, field, replace
import io
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile
import zlib

from opldeck.library import archive_7z
from opldeck.library.archive_7z import PasswordRequired, WrongPassword
from opldeck.core.batch import BatchProgress
from opldeck.core.conversion import BUFFER_SIZE, CancelledError, Progress, _check_cancel, _free_space, _regular_reader
from opldeck.core.formats import FormatError


@dataclass(frozen=True)
class ArchiveLimits:
    total_bytes: int = 32 << 30
    files: int = 10_000
    ratio: int = 2000


DEFAULT_LIMITS = ArchiveLimits()
MAX_ZIP_METADATA = 32 * 1024 * 1024


class _ZipReader:
    """Limit metadata reads before zipfile allocates the central directory."""
    def __init__(self, stream):
        self.stream = stream
        self.metadata = True

    def read(self, size=-1):
        if not self.metadata:
            return self.stream.read(size)
        if size > MAX_ZIP_METADATA:
            raise FormatError('Metadados ZIP excedem o limite de 32 MiB.')
        data = self.stream.read(MAX_ZIP_METADATA + 1 if size < 0 else size)
        if len(data) > MAX_ZIP_METADATA:
            raise FormatError('Metadados ZIP excedem o limite de 32 MiB.')
        return data

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _open_zip(stream):
    reader = _ZipReader(stream)
    archive = zipfile.ZipFile(reader)
    reader.metadata = False
    return archive


def fingerprint(path):
    info = Path(path).stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def detect_format(path):
    path = Path(path)
    if re.search(r'(?i)(?:\.7z\.\d{3,}|\.z\d{2,}|\.zip\.\d{3,})$', path.name):
        raise FormatError('Arquivos compactados em várias partes não são suportados nesta etapa. Reúna as partes e descompacte no PC.')
    with _regular_reader(path) as stream:
        magic = stream.read(8)
    if magic.startswith(b'7z\xbc\xaf\x27\x1c'):
        return '7z'
    if magic[:4] == b'PK\x07\x08':
        raise FormatError('ZIP em várias partes não suportado nesta etapa.')
    if magic[:4] in (b'PK\x03\x04', b'PK\x05\x06', b'PK\x06\x06'):
        return 'zip'
    raise FormatError('Conteúdo não reconhecido como ZIP ou 7z. RAR, TAR e outros formatos não são suportados.')


def safe_member(name):
    path = PurePosixPath(name)
    if (not name or any(ord(char) < 32 for char in name) or '\\' in name or ':' in name
            or path.is_absolute() or '..' in path.parts or not path.parts):
        raise FormatError(f'Caminho inválido no arquivo compactado: {name!r}')
    return Path(*path.parts)


def member_path(info):
    path = safe_member(info.orig_filename)
    mode = info.external_attr >> 16
    if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise FormatError(f'Links e arquivos especiais não são aceitos: {info.filename}')
    return path


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    size: int
    packed: int | None
    directory: bool = False
    encrypted: bool = False


@dataclass(frozen=True)
class ArchivePlan:
    source: Path
    kind: str
    members: tuple[ArchiveMember, ...]
    selected: tuple[str, ...]
    before: tuple
    backend: str = ''
    pairs: tuple[tuple[str, str], ...] = ()
    password: str | None = field(default=None, repr=False, compare=False)

    @property
    def total(self):
        return sum(item.size for item in self.members if item.name in self.selected)

    def select(self, names):
        names = set(names)
        available = {item.name for item in self.members if not item.directory}
        if not names <= available:
            raise FormatError('A seleção contém um arquivo ausente da prévia.')
        # CUE and its referenced BIN form one import unit.
        for cue, binary in self.pairs:
            if cue in names or binary in names:
                names.update((cue, binary))
        games = {item.name for item in self.members if item.name.lower().endswith('.iso')}
        games.update(name for pair in self.pairs for name in pair)
        if not names <= games:
            raise FormatError('Selecione somente ISO ou pares BIN/CUE válidos.')
        return replace(self, selected=tuple(item.name for item in self.members if item.name in names))


@dataclass(frozen=True)
class ArchiveResult:
    directory: Path
    paths: tuple[Path, ...]
    sizes: tuple[int, ...]
    errors: tuple[str, ...] = ()
    sources: tuple[Path, ...] = ()
    groups: tuple[tuple[Path, tuple[Path, ...]], ...] = ()


def validate_members(members, compressed_size, limits):
    if len(members) > limits.files:
        raise FormatError(f'Limite de {limits.files} arquivos excedido.')
    seen = {}
    total = 0
    for item in members:
        path = safe_member(item.name)
        key = path.as_posix().casefold()
        if key in seen:
            raise FormatError('Caminhos repetidos ou conflitantes no arquivo compactado.')
        seen[key] = item.directory
        if item.size < 0:
            raise FormatError('Tamanho negativo no arquivo compactado.')
        total += item.size
        if item.packed is not None and item.size > max(1, item.packed) * limits.ratio:
            raise FormatError(f'Taxa de compressão excede o limite de {limits.ratio}:1: {item.name}')
    for name in seen:
        if any(str(parent) in seen and not seen[str(parent)] for parent in PurePosixPath(name).parents):
            raise FormatError('Caminhos repetidos ou conflitantes no arquivo compactado.')
    if total > limits.total_bytes:
        raise FormatError(f'Tamanho total excede o limite de {limits.total_bytes} bytes.')
    if total > max(1, compressed_size) * limits.ratio:
        raise FormatError(f'Taxa de compressão excede o limite de {limits.ratio}:1.')


class BoundedWriter:
    """Mesmo contrato para stdout do 7z, py7zr e ZIP, sem delegar caminhos."""
    def __init__(self, stream, size, *, cancel=None, report=None):
        self.stream, self.expected = stream, size
        self.cancel, self.report = cancel, report
        self.count = 0

    def write(self, data):
        _check_cancel(self.cancel)
        end = self.stream.tell() + len(data)
        if end > self.expected:
            raise FormatError('Tamanho extraído excede o anunciado.')
        amount = self.stream.write(data)
        if amount != len(data):
            raise OSError('Gravação incompleta durante a extração.')
        self.count = max(self.count, end)
        if self.report:
            self.report(self.count)
        return amount

    def read(self, size=-1):
        return self.stream.read(size)

    def seek(self, offset, whence=0):
        return self.stream.seek(offset, whence)

    def size(self):
        return self.count

    def flush(self):
        self.stream.flush()

    def close(self):
        # Streams remain owned by the caller until every CRC has been checked.
        self.flush()


def read_members(plan, names, factory, *, cancel=None, limits=DEFAULT_LIMITS):
    with _regular_reader(plan.source) as stream:
        info = os.fstat(stream.fileno())
        opened = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if opened != plan.before:
            raise FormatError('Arquivo compactado alterado desde a prévia.')
        if plan.kind == 'zip':
            with _open_zip(stream) as archive:
                for name in names:
                    _check_cancel(cancel)
                    writer = factory(name)
                    try:
                        with archive.open(name, pwd=plan.password.encode('utf-8') if plan.password is not None else None) as source:
                            while data := source.read(BUFFER_SIZE):
                                writer.write(data)
                    except RuntimeError:
                        raise WrongPassword() if plan.password is not None else PasswordRequired() from None
                    except (zipfile.BadZipFile, zlib.error, lzma.LZMAError, EOFError):
                        if plan.password is not None:
                            raise WrongPassword() from None
                        raise FormatError('Dados ZIP corrompidos ou incompletos.') from None
                    writer.flush()
        else:
            archive_7z.extract(stream, names, factory, plan.backend, plan.password, cancel=cancel, limit=limits.total_bytes)
    if fingerprint(plan.source) != plan.before:
        raise FormatError('Arquivo compactado alterado durante a leitura.')


def list_archive(path, *, password=None, backend='auto', limits=DEFAULT_LIMITS, cancel=None):
    path = Path(path).absolute()
    kind = detect_format(path)
    before = fingerprint(path)
    members = []
    selected_backend = ''
    with _regular_reader(path) as stream:
        if kind == 'zip':
            with _open_zip(stream) as archive:
                for info in archive.infolist():
                    _check_cancel(cancel)
                    member_path(info)
                    if info.volume:
                        raise FormatError('ZIP em várias partes não suportado nesta etapa.')
                    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
                        raise FormatError('Método ZIP não suportado pela biblioteca padrão (incluindo ZIP AES).')
                    members.append(ArchiveMember(info.filename, info.file_size, info.compress_size,
                                                 info.is_dir(), bool(info.flag_bits & 1)))
        else:
            with archive_7z.open_7z(stream, password, cancel=cancel, limit=limits.total_bytes) as archive:
                protected = archive.needs_password()
                for info in archive.files:
                    _check_cancel(cancel)
                    if info.is_symlink or info.is_junction or not (info.is_file or info.is_directory):
                        raise FormatError('Links, dispositivos e arquivos especiais não são aceitos no 7z.')
                    members.append(ArchiveMember(info.filename, info.uncompressed, None,
                                                 info.is_directory, protected))
                selected_backend = archive_7z.backend(backend, encrypted=protected)
    validate_members(members, before[2], limits)
    if any(item.encrypted for item in members) and password is None:
        raise PasswordRequired()
    plan = ArchivePlan(path, kind, tuple(members), (), before, selected_backend, password=password)
    files = {item.name: item for item in members if not item.directory}
    pairs = []
    for name, item in files.items():
        if not name.lower().endswith('.cue'):
            continue
        if not 0 < item.size <= 64 * 1024:
            continue
        output = io.BytesIO()
        writer = BoundedWriter(output, item.size, cancel=cancel)
        read_members(plan, [name], lambda _: writer, cancel=cancel, limits=limits)
        try:
            # Parse the FILE reference; full CUE semantics are validated after extraction.
            text = output.getvalue().decode('utf-8-sig')
            references = re.findall(r'^\s*FILE\s+(?:"([^"\r\n]+)"|(\S+))\s+BINARY\s*$', text, re.I | re.M)
            if len(references) != 1:
                continue
            reference = safe_member(references[0][0] or references[0][1])
            binary = (Path(name).parent / reference).as_posix()
            if binary in files and binary.lower().endswith('.bin'):
                pairs.append((name, binary))
        except (UnicodeError, ValueError):
            continue
    selected = {name for name in files if name.lower().endswith('.iso')}
    selected.update(name for pair in pairs for name in pair)
    if not selected:
        raise FormatError('Nenhuma imagem ISO ou par BIN/CUE válido encontrado.')
    plan = replace(plan, pairs=tuple(pairs))
    return plan.select(selected)


def extract_archives(paths, destination, *, cancel=None, progress=None, plans=None, limits=DEFAULT_LIMITS):
    """Um erro de arquivo preserva os anteriores e permite continuar o lote."""
    paths = tuple(dict.fromkeys(map(Path, paths)))
    errors, prepared = [], []
    provided = {str(plan.source): plan for plan in plans or ()}
    for path in paths:
        _check_cancel(cancel)
        try:
            plan = provided.get(str(path.absolute())) or list_archive(path, cancel=cancel, limits=limits)
            if plan.selected:
                prepared.append(plan)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            if len(paths) == 1:
                raise
            errors.append(f'{path.name}: {exc}')
    if not prepared:
        raise FormatError('Nenhum arquivo pronto para extração.\n' + '\n'.join(errors))
    total = sum(plan.total for plan in prepared)
    if total > limits.total_bytes:
        raise FormatError(f'Lote excede o limite de {limits.total_bytes} bytes.')
    destination = Path(destination).resolve(strict=True)
    _free_space(destination, total + 65536)
    directory = None
    outputs, sizes, sources, groups = [], [], [], []
    done, position = 0, 0
    count = sum(len(plan.selected) for plan in prepared)
    try:
        directory = Path(tempfile.mkdtemp(prefix='OPLDeck-extraidos-', dir=destination))
        for archive_index, plan in enumerate(prepared, 1):
            _check_cancel(cancel)
            subdir = directory / f'{archive_index:03d}'
            streams, writers, allocated = {}, {}, {}
            members = {item.name: item for item in plan.members}
            start_position, start_done = position, done
            indexes = {name: position + index for index, name in enumerate(plan.selected, 1)}
            counts = {name: 0 for name in plan.selected}
            def report(name, copied, phase='transfer'):
                counts[name] = copied
                if progress:
                    progress(BatchProgress(indexes[name], count, f'{plan.source.name} / {name}',
                                           'Extração ZIP' if plan.kind == 'zip' else 'Extração 7z',
                                           Progress(copied, members[name].size, 1, 1),
                                           start_done + sum(counts.values()), total * 2, phase))
            def factory(name):
                if name not in plan.selected or name in writers:
                    raise FormatError('O extrator solicitou arquivo fora da seleção ou repetido.')
                target = subdir / safe_member(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = target.open('x+b')
                streams[name] = stream
                allocated[name] = target
                writer = BoundedWriter(stream, members[name].size, cancel=cancel,
                                       report=lambda copied: report(name, copied))
                writers[name] = writer
                report(name, 0, 'preparing')
                return writer
            try:
                read_members(plan, plan.selected, factory, cancel=cancel, limits=limits)
                for name in plan.selected:
                    if name not in writers or writers[name].count != members[name].size:
                        raise FormatError(f'Extração incompleta: {name}')
                    streams[name].flush()
                    os.fsync(streams[name].fileno())
                    report(name, members[name].size, 'complete')
                if fingerprint(plan.source) != plan.before:
                    raise FormatError('Arquivo compactado alterado durante a leitura.')
                outputs.extend(allocated[name] for name in plan.selected)
                sizes.extend(members[name].size for name in plan.selected)
                sources.append(plan.source)
                groups.append((plan.source, tuple(allocated[name] for name in plan.selected)))
            except CancelledError:
                raise
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                if len(paths) == 1:
                    raise
                errors.append(f'{plan.source.name}: {exc}')
                for stream in streams.values():
                    stream.close()
                if subdir.exists():
                    shutil.rmtree(subdir)
            finally:
                for stream in streams.values():
                    stream.close()
            done += plan.total
            position += len(plan.selected)
        _check_cancel(cancel)
        if not outputs:
            raise FormatError('Nenhuma imagem extraída.\n' + '\n'.join(errors))
        return ArchiveResult(directory, tuple(outputs), tuple(sizes), tuple(errors), tuple(sources), tuple(groups))
    except Exception as exc:
        if directory is not None:
            try:
                shutil.rmtree(directory)
            except OSError as cleanup:
                exc.add_note(f'Não foi possível limpar a pasta de extração {directory}: {cleanup}')
        raise
