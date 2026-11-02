"""Leitura e serialização de ul.cfg. Nenhuma escrita em disco implícita."""

from dataclasses import dataclass, field
from pathlib import Path

from opldeck.core.crc import usba_crc32
from opldeck.core.formats import FormatError, Media, validate_game_id
from opldeck.library.fileio import regular_reader

RECORD_SIZE = 64
MAX_CFG_SIZE = RECORD_SIZE * 100_000
DEFAULT_RESERVED = bytes(4) + b"\x08" + bytes(10)


@dataclass(frozen=True)
class ULEntry:
    title: str
    game_id: str
    parts: int
    media: Media
    reserved: bytes = field(default=DEFAULT_RESERVED, repr=False)

    def __post_init__(self) -> None:
        usba_crc32(self.title)
        if not self.title or any(ord(c) < 32 or ord(c) == 127 for c in self.title):
            raise FormatError("O título UL deve conter de 1 a 32 caracteres ASCII imprimíveis.")
        validate_game_id(self.game_id)
        if not 1 <= self.parts <= 255:
            raise FormatError("Número de partes UL inválido (1 a 255).")
        try:
            object.__setattr__(self, "media", Media(self.media))
        except ValueError as exc:
            raise FormatError("Mídia UL inválida: esperado CD (0x12) ou DVD (0x14).") from exc
        if len(self.reserved) != 15:
            raise FormatError("Campos reservados UL devem ocupar 15 bytes.")

    @property
    def crc(self) -> int:
        return usba_crc32(self.title)

    def part_name(self, index: int) -> str:
        if not 0 <= index < self.parts:
            raise FormatError("Índice de parte fora do intervalo do jogo.")
        return f"ul.{self.crc:08X}.{self.game_id}.{index:02x}"

    def to_bytes(self) -> bytes:
        return (self.title.encode("ascii").ljust(32, b"\0") + b"ul."
                + self.game_id.encode("ascii").ljust(12, b"\0")
                + bytes((self.parts, self.media)) + self.reserved)


def parse_cfg(data: bytes) -> list[ULEntry]:
    if len(data) > MAX_CFG_SIZE:
        raise FormatError("ul.cfg excede o limite de leitura de 100.000 registros.")
    if len(data) % RECORD_SIZE:
        raise FormatError("ul.cfg truncado: o tamanho deve ser múltiplo de 64 bytes.")
    entries = []
    for offset in range(0, len(data), RECORD_SIZE):
        record = data[offset:offset + RECORD_SIZE]
        try:
            if record[32:35] != b"ul.":
                raise FormatError("Assinatura 'ul.' ausente.")
            entries.append(ULEntry(
                record[:32].split(b"\0", 1)[0].decode("ascii"),
                record[35:47].split(b"\0", 1)[0].decode("ascii"),
                record[47], record[48], record[49:64],
            ))
        except (ValueError, UnicodeError) as exc:
            raise FormatError(f"Registro {offset // RECORD_SIZE + 1}: {exc}") from exc
    return entries


def read_cfg(path: str | Path) -> list[ULEntry]:
    with regular_reader(Path(path)) as stream:
        return parse_cfg(stream.read(MAX_CFG_SIZE + 1))


def serialize_cfg(entries: list[ULEntry]) -> bytes:
    return b"".join(entry.to_bytes() for entry in entries)
