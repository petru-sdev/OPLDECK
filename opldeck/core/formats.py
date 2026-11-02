"""Valores e validações compartilhados, sem dependência de interface gráfica."""

from enum import IntEnum
import re

PART_SIZE = 1 << 30
CD_SIZE_THRESHOLD = 0x2BC10000
GAME_ID = re.compile(r"[A-Z]{4}_[0-9]{3}\.[0-9]{2}")


class FormatError(ValueError):
    """Dados inválidos ou formato ainda não suportado."""


class Media(IntEnum):
    CD = 0x12
    DVD = 0x14


def validate_game_id(game_id: str) -> str:
    if not GAME_ID.fullmatch(game_id):
        raise FormatError(f"ID de jogo inválido: {game_id!r} (esperado SLUS_123.45).")
    return game_id


def part_count(size: int) -> int:
    if size <= 0:
        raise FormatError("A imagem deve conter dados.")
    count = (size + PART_SIZE - 1) // PART_SIZE
    if count > 255:
        raise FormatError("A imagem excede o limite de 255 partes UL.")
    return count
