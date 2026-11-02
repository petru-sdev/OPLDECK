"""Leitor ISO9660 limitado ao SYSTEM.CNF da raiz, implementado do zero."""

from dataclasses import dataclass
from pathlib import Path
import os
import re
from typing import BinaryIO

from opldeck.core.formats import CD_SIZE_THRESHOLD, FormatError, Media, part_count
from opldeck.library.diagnostics import iso_name_parts, iso_warnings
from opldeck.library.fileio import regular_reader

SECTOR = 2048
MAX_ROOT_SIZE = 8 * 1024 * 1024
MAX_CNF_SIZE = 64 * 1024


def parse_system_cnf(data: bytes) -> str:
    text = data.decode("ascii", errors="replace")
    match = re.search(
        r'^\s*BOOT2\s*=\s*"?cdrom0:[\\/]+([A-Z]{4}_[0-9]{3}\.[0-9]{2});1"?\s*$',
        text, re.IGNORECASE | re.MULTILINE,
    )
    if not match:
        raise FormatError("SYSTEM.CNF não contém BOOT2 com ID de PS2 válido.")
    return match.group(1).upper()


def _dual(data: bytes, offset: int, width: int) -> int:
    if len(data) < offset + 2 * width:
        raise FormatError(f'Campo ISO9660 truncado: offset={offset}, largura={width}, disponíveis={len(data)}.')
    little = int.from_bytes(data[offset:offset + width], "little", signed=False)
    big = int.from_bytes(data[offset + width:offset + 2 * width], "big", signed=False)
    if little != big:
        raise FormatError(f"Campos little-endian/big-endian inconsistentes na ISO: offset={offset}, LE={little}, BE={big}.")
    return little


def _read(stream: BinaryIO, offset: int, length: int, size: int) -> bytes:
    if offset < 0 or length < 0 or offset + length > size:
        raise FormatError("ISO truncada: extensão fora dos limites do arquivo.")
    stream.seek(offset)
    data = stream.read(length)
    if len(data) != length:
        raise FormatError("ISO truncada durante a leitura.")
    return data


def _record(data: bytes) -> tuple[int, int, int, bytes]:
    if len(data) < 34 or data[0] != len(data) or 33 + data[32] > len(data):
        raise FormatError("Registro de diretório ISO9660 inválido.")
    if data[26] or data[27] or data[25] & 0x80:
        raise FormatError("Arquivos intercalados ou com múltiplas extensões não são suportados.")
    extent = _dual(data, 2, 4) + data[1]
    length = _dual(data, 10, 4)
    return extent, length, data[25], data[33:33 + data[32]]


def read_game_id(path: str | Path) -> str:
    with regular_reader(Path(path)) as stream:
        before = os.fstat(stream.fileno())
        result = read_game_id_stream(stream)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise FormatError(f'ISO alterada durante a identificação: tamanho inicial={before.st_size}, final={after.st_size} bytes. Aguarde a cópia ou extração terminar.')
        return result


def read_game_id_stream(stream: BinaryIO) -> str:
    """Identifica PS2 em um fluxo seekable de setores lógicos de 2048 bytes."""
    stream.seek(0, 2)
    size = stream.tell()
    pvd = None
    for sector in range(16, 80):
        descriptor = _read(stream, sector * SECTOR, SECTOR, size)
        if descriptor[1:7] != b"CD001\x01":
            raise FormatError("A imagem não contém um descritor ISO9660 válido.")
        if descriptor[0] == 1:
            pvd = descriptor
            break
        if descriptor[0] == 255:
            break
    if pvd is None:
        raise FormatError("Descritor primário ISO9660 não encontrado.")
    if _dual(pvd, 128, 2) != SECTOR:
        raise FormatError("Somente ISOs com setores de 2048 bytes são suportadas.")
    sectors = _dual(pvd, 80, 4)
    volume_size = sectors * SECTOR  # Python int: sem overflow com sinal acima de 2 GiB.
    if not volume_size or volume_size > size:
        raise FormatError(f"ISO truncada: tamanho do volume inválido. Volume={volume_size} bytes "
                          f"({sectors} setores × {SECTOR}); arquivo={size} bytes; "
                          f"diferença={volume_size - size} bytes. Aguarde a cópia ou extração terminar.")
    extent, length, flags, _ = _record(pvd[156:156 + pvd[156]])
    if not flags & 2 or not 0 < length <= MAX_ROOT_SIZE:
        raise FormatError("Diretório raiz ISO9660 inválido ou muito grande.")
    root = _read(stream, extent * SECTOR, length, volume_size)
    offset = 0
    while offset < len(root):
        record_size = root[offset]
        if record_size == 0:
            offset = (offset // SECTOR + 1) * SECTOR
            continue
        if offset % SECTOR + record_size > SECTOR:
            raise FormatError("Registro de diretório cruza o limite do setor.")
        block, file_size, flags, name = _record(root[offset:offset + record_size])
        if name.upper() in (b"SYSTEM.CNF;1", b"SYSTEM.CNF") and not flags & 2:
            if not 0 < file_size <= MAX_CNF_SIZE:
                raise FormatError("SYSTEM.CNF vazio ou maior que 64 KiB.")
            return parse_system_cnf(_read(stream, block * SECTOR, file_size, volume_size))
        offset += record_size
    raise FormatError("SYSTEM.CNF não encontrado na raiz da ISO.")


@dataclass(frozen=True)
class ISOGame:
    path: Path
    title: str
    game_id: str
    size: int
    media: Media
    media_estimated: bool
    parts: int
    warning: str = ""


def infer_media(path, size):
    """Pasta tem prioridade; o corte por tamanho é uma heurística, não prova da mídia."""
    folder = Path(path).parent.name.upper()
    estimated = folder not in ('CD', 'DVD')
    return (Media[folder] if not estimated else
            Media.CD if size < CD_SIZE_THRESHOLD else Media.DVD), estimated


def inspect_iso(path: str | Path) -> ISOGame:
    path = Path(path).resolve()
    if path.suffix.lower() != ".iso":
        raise FormatError("Esperado arquivo .iso; use a conversão BIN/CUE para imagens raw. ZSO não suportado.")
    size = path.stat().st_size
    parts = part_count(size)
    game_id = read_game_id(path)
    title, _ = iso_name_parts(path)
    warning = '\n'.join(iso_warnings(path, game_id))
    media, estimated = infer_media(path, size)
    return ISOGame(path, title, game_id, size, media, estimated, parts, warning)
