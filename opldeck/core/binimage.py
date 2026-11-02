"""Leitura e conversão de uma faixa de dados PS2 raw, sem código de terceiros.

Layout de referência: libcdio include/cdio/sector.h (Mode 1 e XA Form 1).
Valida estrutura dos setores, não recalcula EDC/ECC nem corrige a imagem.
"""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
from threading import Event
from typing import Callable

from opldeck.core.conversion import (
    BUFFER_SIZE, Progress, _buffer_check, _check_cancel, _free_space,
    _regular_reader, _remove_created, _snapshot, _sync_directory, _track, directory_lock,
)
from opldeck.core.formats import FormatError
from opldeck.core.iso import SECTOR, read_game_id, read_game_id_stream

RAW_SECTOR = 2352
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
MAX_CUE_SIZE = 64 * 1024
logger = logging.getLogger(__name__)


def _sector_error(index: int, message: str) -> FormatError:
    return FormatError(f"Setor raw {index} (offset 0x{index * RAW_SECTOR:X}): {message}")


def sector_payload(raw: bytes, index: int, expected_mode: int | None = None) -> tuple[int, bytes]:
    """Retorna os 2048 bytes úteis ou rejeita estruturas não suportadas."""
    if len(raw) != RAW_SECTOR:
        raise _sector_error(index, "setor truncado; esperados 2352 bytes.")
    if raw[:12] != SYNC:
        raise _sector_error(index, "sincronismo inválido; áudio e imagens sem cabeçalho raw não são suportados.")
    mode = raw[15]
    if mode not in (1, 2):
        raise _sector_error(index, f"modo {mode} não suportado.")
    if expected_mode is not None and mode != expected_mode:
        raise _sector_error(index, f"mudança de modo {expected_mode} → {mode}; faixa mista não suportada.")
    if mode == 2:
        if raw[16:20] != raw[20:24]:
            raise _sector_error(index, "as duas cópias do subcabeçalho XA divergem.")
        if raw[18] & 0x20:
            raise _sector_error(index, "Mode 2 Form 2 contém 2324 bytes úteis e não pode ser convertido para ISO de 2048 bytes.")
    offset = 16 if mode == 1 else 24
    return mode, raw[offset:offset + SECTOR]


def cue_source(path: Path) -> tuple[Path, int]:
    """CUE UTF-8, um FILE BINARY / TRACK 01 / INDEX 01 em 00:00:00."""
    with _regular_reader(path) as stream:
        data = stream.read(MAX_CUE_SIZE + 1)
    if len(data) > MAX_CUE_SIZE:
        raise FormatError("CUE excede o limite de 64 KiB.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise FormatError("O CUE deve estar codificado em UTF-8.") from exc
    filename = None
    mode = None
    indexed = False
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        key = line.split()[0].upper()
        if key in {"REM", "TITLE", "PERFORMER", "SONGWRITER", "CATALOG"}:
            continue
        if key == "FILE":
            match = re.fullmatch(r'FILE\s+(?:"([^"\r\n]+)"|(\S+))\s+BINARY', line, re.IGNORECASE)
            if filename is not None or mode is not None or not match:
                raise FormatError(f"CUE linha {line_number}: esperado um único FILE BINARY.")
            filename = (match.group(1) or match.group(2)).replace("\\", "/")
        elif key == "TRACK":
            match = re.fullmatch(r"TRACK\s+01\s+MODE([12])/2352", line, re.IGNORECASE)
            if filename is None or mode is not None or not match:
                raise FormatError(f"CUE linha {line_number}: suportada somente TRACK 01 MODE1/2352 ou MODE2/2352.")
            mode = int(match.group(1))
        elif key == "INDEX":
            if mode is None or indexed or not re.fullmatch(r"INDEX\s+01\s+00:00:00", line, re.IGNORECASE):
                raise FormatError(f"CUE linha {line_number}: suportado somente INDEX 01 00:00:00, sem pregap.")
            indexed = True
        else:
            raise FormatError(f"CUE linha {line_number}: comando {key} não suportado.")
    if filename is None or mode is None or not indexed:
        raise FormatError("CUE incompleto: FILE, TRACK e INDEX são obrigatórios.")
    relative = Path(filename)
    if "\0" in filename or relative.is_absolute() or ".." in relative.parts or ":" in filename:
        raise FormatError("O BIN indicado no CUE deve estar dentro da pasta do CUE.")
    source = (path.parent / relative).resolve(strict=True)
    if not source.is_relative_to(path.parent.resolve()) or source.suffix.lower() != ".bin":
        raise FormatError("O CUE deve apontar para um arquivo .bin dentro de sua pasta.")
    return source, mode


class RawISOReader:
    """Adaptador de leitura randômica raw → setores lógicos; não possui o stream."""

    def __init__(self, stream, raw_size: int, mode: int):
        self.stream = stream
        self.mode = mode
        self.size = raw_size // RAW_SECTOR * SECTOR
        self.position = 0

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence not in (0, 1, 2):
            raise ValueError("whence inválido")
        position = offset + (0 if whence == 0 else self.position if whence == 1 else self.size)
        if position < 0:
            raise ValueError("Posição negativa")
        self.position = position
        return position

    def read(self, length: int = -1) -> bytes:
        remaining = max(0, self.size - self.position)
        length = remaining if length < 0 else min(length, remaining)
        output = bytearray()
        while length:
            index, offset = divmod(self.position, SECTOR)
            self.stream.seek(index * RAW_SECTOR)
            _, data = sector_payload(self.stream.read(RAW_SECTOR), index, self.mode)
            count = min(length, SECTOR - offset)
            output.extend(data[offset:offset + count])
            self.position += count
            length -= count
        return bytes(output)


@dataclass(frozen=True)
class BINGame:
    path: Path
    bin_path: Path
    title: str
    game_id: str
    raw_size: int
    iso_size: int
    sectors: int
    mode: int
    payload_offset: int

    @property
    def sector_format(self) -> str:
        return "Mode 1" if self.mode == 1 else "Mode 2 Form 1"


def inspect_bin(path: str | Path) -> BINGame:
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() == ".cue":
        source, expected_mode = cue_source(path)
    elif path.suffix.lower() == ".bin":
        source, expected_mode = path, None
    else:
        raise FormatError("Esperado arquivo .bin raw ou .cue de faixa única.")
    with _regular_reader(source) as stream:
        original = os.fstat(stream.fileno())
        if not original.st_size or original.st_size % RAW_SECTOR:
            raise FormatError("BIN vazio ou truncado: tamanho deve ser múltiplo de 2352 bytes.")
        mode, _ = sector_payload(stream.read(RAW_SECTOR), 0, expected_mode)
        reader = RawISOReader(stream, original.st_size, mode)
        game_id = read_game_id_stream(reader)
        if _snapshot(os.fstat(stream.fileno())) != _snapshot(original):
            raise FormatError("BIN alterado durante a identificação.")
    sectors = original.st_size // RAW_SECTOR
    return BINGame(path, source, source.stem, game_id, original.st_size, sectors * SECTOR,
                   sectors, mode, 16 if mode == 1 else 24)


def convert_bin(source: str | Path, destination: str | Path, *, cancel: Event | None = None,
                progress: Callable[[Progress], None] | None = None,
                buffer_size: int = BUFFER_SIZE) -> Path:
    """Extrai todos os setores de uma faixa PS2; falhas removem a nova ISO parcial."""
    _buffer_check(buffer_size)
    _check_cancel(cancel)
    game = inspect_bin(source)
    destination = Path(destination).absolute()
    if destination.suffix.lower() != ".iso":
        raise FormatError("O destino deve ser um novo arquivo .iso.")
    root = destination.parent.resolve(strict=True)
    created = []
    sectors_per_buffer = max(1, buffer_size // RAW_SECTOR)
    logger.info("BIN → ISO | origem=%s | BIN=%s | destino=%s | ID=%s | formato=%s | "
                "raw=%d bytes | ISO=%d bytes | setores=%d | offset_payload=%d | buffer=%d",
                game.path, game.bin_path, destination, game.game_id, game.sector_format,
                game.raw_size, game.iso_size, game.sectors, game.payload_offset, buffer_size)
    logger.info("Verificação estrutural de todos os setores; EDC/ECC não são verificados nem corrigidos.")
    with directory_lock(root) as directory_fd:
        if os.path.lexists(destination):
            raise FileExistsError(f"Destino já existe: {destination}")
        _free_space(root, game.iso_size)
        try:
            with _regular_reader(game.bin_path) as stream:
                original = os.fstat(stream.fileno())
                if original.st_size != game.raw_size:
                    raise FormatError("BIN alterado desde a identificação.")
                # Confere novamente o ID no mesmo descritor usado na conversão.
                if read_game_id_stream(RawISOReader(stream, original.st_size, game.mode)) != game.game_id:
                    raise FormatError("ID do BIN alterado desde a identificação.")
                stream.seek(0)
                with destination.open("xb") as output:
                    _track(destination, output, created)
                    index = 0
                    while index < game.sectors:
                        _check_cancel(cancel)
                        count = min(sectors_per_buffer, game.sectors - index)
                        raw = stream.read(count * RAW_SECTOR)
                        if len(raw) != count * RAW_SECTOR:
                            raise _sector_error(index, "BIN truncado durante a conversão.")
                        data = bytearray()
                        for local in range(count):
                            _check_cancel(cancel)
                            _, payload = sector_payload(raw[local * RAW_SECTOR:(local + 1) * RAW_SECTOR],
                                                        index + local, game.mode)
                            data.extend(payload)
                        if output.write(data) != len(data):
                            raise OSError("Gravação incompleta da ISO de destino.")
                        index += count
                        if progress:
                            progress(Progress(index * SECTOR, game.iso_size, 1, 1))
                    _check_cancel(cancel)
                    if _snapshot(os.fstat(stream.fileno())) != _snapshot(original):
                        raise FormatError("BIN alterado durante a conversão.")
                    if progress:
                        progress(Progress(game.iso_size, game.iso_size, 1, 1, 'sync'))
                    output.flush()
                    os.fsync(output.fileno())
            if read_game_id(destination) != game.game_id:
                raise FormatError("O ID da ISO resultante não corresponde ao BIN.")
            _sync_directory(directory_fd)
            _check_cancel(cancel)
        except BaseException as exc:
            _remove_created(created, exc)
            logger.info("Conversão BIN desfeita; limpeza da ISO parcial executada.")
            raise
    logger.info("ISO concluída e identificada | ID=%s | bytes=%d | %s", game.game_id, game.iso_size, destination)
    return destination
