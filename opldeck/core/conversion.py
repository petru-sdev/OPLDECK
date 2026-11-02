"""Conversões em streaming com criação exclusiva e publicação atômica de ul.cfg.

O lock de diretório coordena instâncias deste aplicativo (Linux). Outros programas
devem permanecer fechados durante a conversão. Falhas capturadas desfazem apenas
os arquivos criados pela operação; encerramento forçado pode deixar órfãos.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import logging
import os
from pathlib import Path
import shutil
import tempfile
from threading import Event
from typing import Callable

from opldeck.core.formats import FormatError, Media, PART_SIZE
from opldeck.library.fileio import regular_reader as _regular_reader
from opldeck.core.iso import inspect_iso
from opldeck.core.ul import MAX_CFG_SIZE, ULEntry, parse_cfg

BUFFER_SIZE = 4 * 1024 * 1024
TRANSACTION_DIR = ".opldeck-transaction"
logger = logging.getLogger(__name__)


class CancelledError(Exception):
    """Operação cancelada antes da publicação."""


@dataclass(frozen=True)
class Progress:
    completed: int
    total: int
    part: int
    parts: int
    phase: str = 'transfer'


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError("Conversão cancelada; arquivos parciais removidos.")


def require_clean_library(directory: Path) -> None:
    if os.path.lexists(directory / TRANSACTION_DIR):
        raise FormatError("Operação UL pendente. Use 'Resolver operação pendente…' ou recover-ul antes de continuar.")


@contextmanager
def directory_lock(directory: Path):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FormatError("Outra operação OPL Deck está usando este diretório.") from exc
        yield descriptor
    finally:
        os.close(descriptor)


def _sync_directory(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno != errno.EINVAL:  # Alguns sistemas não implementam fsync em diretórios.
            raise


def _snapshot(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _cfg_bytes(path: Path) -> bytes | None:
    try:
        with _regular_reader(path) as stream:
            data = stream.read(MAX_CFG_SIZE + 1)
    except FileNotFoundError:
        return None
    parse_cfg(data)
    return data


def _free_space(directory: Path, needed: int) -> None:
    free = shutil.disk_usage(directory).free
    logger.info("Espaço no destino %s | livre=%d bytes | necessário=%d bytes", directory, free, needed)
    if free < needed:
        raise FormatError(f"Espaço insuficiente no destino: necessários {needed:,} bytes.")


def _buffer_check(size: int) -> None:
    if not 4096 <= size <= 64 * 1024 * 1024:
        raise FormatError("Buffer deve ter entre 4 KiB e 64 MiB.")


def _copy(source, target, length, buffer_size, cancel, notify):
    remaining = length
    while remaining:
        _check_cancel(cancel)
        data = source.read(min(remaining, buffer_size))
        if not data:
            raise FormatError("Arquivo de origem truncado durante a conversão.")
        written = target.write(data)
        if written != len(data):
            raise OSError("Gravação incompleta no destino.")
        remaining -= len(data)
        notify(len(data))
    _check_cancel(cancel)


def _remove_created(created, error):
    for path, identity in reversed(created):
        try:
            info = path.lstat()
            if (info.st_dev, info.st_ino) == identity:
                path.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup_error:
            error.add_note(f"Não foi possível remover o parcial {path}: {cleanup_error}")


def _track(path, stream, created):
    info = os.fstat(stream.fileno())
    created.append((path, (info.st_dev, info.st_ino)))


def install_iso(source: str | Path, destination: str | Path, *, title: str | None = None,
                media: Media | None = None, cancel: Event | None = None,
                progress: Callable[[Progress], None] | None = None,
                buffer_size: int = BUFFER_SIZE) -> ULEntry:
    """Instala uma ISO identificável. Destino deve existir; origem é preservada."""
    _buffer_check(buffer_size)
    _check_cancel(cancel)
    source = Path(source).resolve()
    destination = Path(destination).resolve(strict=True)
    original = source.stat()
    game = inspect_iso(source)
    if media is None and game.media_estimated:
        raise FormatError("Confirme a mídia CD ou DVD para instalar uma ISO fora dessas pastas.")
    entry = ULEntry(game.title if title is None else title, game.game_id,
                    game.parts, game.media if media is None else media)
    logger.info("ISO → UL | origem=%s | destino=%s | ID=%s | título=%s | mídia=%s | "
                "bytes=%d | partes=%d | CRC=%08X | buffer=%d", source, destination,
                entry.game_id, entry.title, entry.media.name, game.size, entry.parts, entry.crc, buffer_size)
    cfg = destination / "ul.cfg"
    created = []
    committed = False
    done = 0

    with directory_lock(destination) as directory_fd:
        require_clean_library(destination)
        previous = _cfg_bytes(cfg)
        entries = parse_cfg(previous or b"")
        if any(e.title.casefold() == entry.title.casefold() for e in entries):
            raise FormatError("Já existe um jogo UL com este título.")
        if any(e.game_id == entry.game_id for e in entries):
            raise FormatError("Já existe um jogo UL com este ID.")
        updated = (previous or b"") + entry.to_bytes()
        if len(updated) > MAX_CFG_SIZE:
            raise FormatError("ul.cfg atingiu o limite de registros suportados.")
        paths = [destination / entry.part_name(index) for index in range(entry.parts)]
        if any(os.path.lexists(path) for path in paths):
            raise FileExistsError("Parte UL já existe no destino; nenhum arquivo foi sobrescrito.")
        _free_space(destination, game.size + len(updated))
        try:
            with _regular_reader(source) as input_stream:
                if _snapshot(os.fstat(input_stream.fileno())) != _snapshot(original):
                    raise FormatError("A ISO foi alterada durante a identificação.")
                for index, path in enumerate(paths):
                    _check_cancel(cancel)
                    logger.info("Gravando parte %d/%d: %s", index + 1, entry.parts, path)

                    def notify(amount):
                        nonlocal done
                        done += amount
                        if progress:
                            progress(Progress(done, game.size, index + 1, entry.parts))

                    with path.open("xb") as output:
                        _track(path, output, created)
                        _copy(input_stream, output, min(PART_SIZE, game.size - done),
                              buffer_size, cancel, notify)
                        if progress:
                            progress(Progress(done, game.size, index + 1, entry.parts, 'sync'))
                        output.flush()
                        os.fsync(output.fileno())
                if _snapshot(os.fstat(input_stream.fileno())) != _snapshot(original):
                    raise FormatError("A ISO foi alterada durante a conversão.")
            # Preserva os bytes anteriores, inclusive padding e campos desconhecidos.
            if progress:
                progress(Progress(done, game.size, entry.parts, entry.parts, 'sync'))
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".opldeck-cfg-", dir=destination,
                                             delete=False) as temporary:
                temporary_path = Path(temporary.name)
                _track(temporary_path, temporary, created)
                temporary.write(updated)
                temporary.flush()
                os.fsync(temporary.fileno())
            _sync_directory(directory_fd)
            _check_cancel(cancel)
            if _cfg_bytes(cfg) != previous:
                raise FormatError("ul.cfg foi alterado por outro programa; instalação desfeita.")
            os.replace(temporary_path, cfg)
            committed = True
            logger.info("Índice publicado: %s | registros=%d", cfg, len(entries) + 1)
            _sync_directory(directory_fd)
        except BaseException as exc:
            if not committed:
                _remove_created(created, exc)
                logger.info("Instalação desfeita; limpeza dos arquivos criados executada.")
            else:
                exc.add_note("O jogo já foi publicado em ul.cfg; os arquivos foram preservados.")
            raise
    return entry


def export_ul(cfg_path: str | Path, game_id: str, destination: str | Path, *,
              cancel: Event | None = None, progress: Callable[[Progress], None] | None = None,
              buffer_size: int = BUFFER_SIZE) -> Path:
    """Reconstrói uma ISO em um novo arquivo. Nunca substitui um destino existente."""
    _buffer_check(buffer_size)
    _check_cancel(cancel)
    cfg_path = Path(cfg_path).absolute()
    destination = Path(destination).absolute()
    if destination.suffix.lower() != ".iso":
        raise FormatError("O destino deve ser um novo arquivo .iso.")
    root = cfg_path.parent.resolve(strict=True)
    target_root = destination.parent.resolve(strict=True)
    created = []
    with directory_lock(root):
        require_clean_library(root)
        entries = parse_cfg(_cfg_bytes(cfg_path) or b"")
        matches = [entry for entry in entries if entry.game_id == game_id]
        if len(matches) != 1:
            raise FormatError("ID não encontrado ou duplicado em ul.cfg.")
        entry = matches[0]
        parts = []
        for index in range(entry.parts):
            path = root / entry.part_name(index)
            with _regular_reader(path) as stream:
                info = os.fstat(stream.fileno())
            expected = PART_SIZE if index < entry.parts - 1 else info.st_size
            if info.st_size != expected or not 0 < info.st_size <= PART_SIZE:
                raise FormatError(f"Tamanho inválido da parte: {path.name}")
            parts.append((path, info))
        if os.path.lexists(destination):
            raise FileExistsError(f"Destino já existe: {destination}")
        total = sum(info.st_size for _, info in parts)
        logger.info("UL → ISO | índice=%s | ID=%s | destino=%s | bytes=%d | partes=%d | buffer=%d",
                    cfg_path, game_id, destination, total, entry.parts, buffer_size)
        _free_space(target_root, total)
        done = 0
        try:
            with destination.open("xb") as output:
                _track(destination, output, created)
                for index, (path, original) in enumerate(parts):
                    logger.info("Lendo parte %d/%d: %s", index + 1, entry.parts, path)

                    def notify(amount):
                        nonlocal done
                        done += amount
                        if progress:
                            progress(Progress(done, total, index + 1, entry.parts))

                    with _regular_reader(path) as source:
                        if _snapshot(os.fstat(source.fileno())) != _snapshot(original):
                            raise FormatError(f"Parte alterada durante a conversão: {path.name}")
                        _copy(source, output, original.st_size, buffer_size, cancel, notify)
                        if _snapshot(os.fstat(source.fileno())) != _snapshot(original):
                            raise FormatError(f"Parte alterada durante a conversão: {path.name}")
                if progress:
                    progress(Progress(done, total, entry.parts, entry.parts, 'sync'))
                output.flush()
                os.fsync(output.fileno())
                descriptor = os.open(target_root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    _sync_directory(descriptor)
                finally:
                    os.close(descriptor)
                _check_cancel(cancel)
        except BaseException as exc:
            _remove_created(created, exc)
            logger.info("Exportação desfeita; limpeza do arquivo parcial executada.")
            raise
    logger.info("ISO concluída: %s | bytes=%d", destination, total)
    return destination
