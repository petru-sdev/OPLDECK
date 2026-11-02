"""CRC USBExtreme: MSB-first, estado inicial zero e NUL incluído.

Referência de interoperabilidade: OPL src/system.c, USBA_crc32.
Usa inteiros sem sinal explícitos para evitar o overflow de signed int do C.
"""

from opldeck.core.formats import FormatError


def _make_table() -> tuple[int, ...]:
    table = [0] * 256
    for value in range(256):
        crc = value << 24
        for _ in range(8):
            high = crc & 0x80000000
            crc = (crc << 1) & 0xFFFFFFFF
            if not high:
                crc ^= 0x04C11DB7
        table[255 - value] = crc
    return tuple(table)


_TABLE = _make_table()


def usba_crc32(title: str) -> int:
    try:
        data = title.encode("ascii")
    except UnicodeEncodeError as exc:
        raise FormatError("O título UL deve conter somente caracteres ASCII.") from exc
    if len(data) > 32 or b"\0" in data:
        raise FormatError("O título UL deve ter até 32 bytes, sem NUL interno.")
    crc = 0
    for byte in data + b"\0":
        crc = _TABLE[byte ^ (crc >> 24)] ^ ((crc << 8) & 0xFFFFFFFF)
    return crc
