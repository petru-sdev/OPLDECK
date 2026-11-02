"""Imagens mínimas e sintéticas: não contêm dados de jogos comerciais."""

from pathlib import Path

SECTOR = 2048


def dual(value: int, width: int) -> bytes:
    return value.to_bytes(width, "little") + value.to_bytes(width, "big")


def record(name: bytes, block: int, length: int, flags: int = 0) -> bytes:
    size = 33 + len(name) + (len(name) % 2 == 0)
    result = bytearray(size)
    result[0] = size
    result[2:10] = dual(block, 4)
    result[10:18] = dual(length, 4)
    result[25] = flags
    result[28:32] = dual(1, 2)
    result[32] = len(name)
    result[33:33 + len(name)] = name
    return bytes(result)


def make_iso(path: Path, game_id="SLUS_123.45", second_sector=False, boot_record=False) -> Path:
    image = bytearray(24 * SECTOR)
    pvd_sector = 17 if boot_record else 16
    if boot_record:
        image[16 * SECTOR:16 * SECTOR + 7] = b"\x00CD001\x01"
    pvd = bytearray(SECTOR)
    pvd[:7] = b"\x01CD001\x01"
    pvd[80:88] = dual(24, 4)
    pvd[128:132] = dual(SECTOR, 2)
    root = record(b"\x00", 20, 2 * SECTOR, 2)
    pvd[156:156 + len(root)] = root
    image[pvd_sector * SECTOR:(pvd_sector + 1) * SECTOR] = pvd
    image[(pvd_sector + 1) * SECTOR:(pvd_sector + 1) * SECTOR + 7] = b"\xffCD001\x01"
    cnf = f"BOOT2 = cdrom0:\\{game_id};1\r\nVER = 1.00\r\nVMODE = NTSC\r\n".encode("ascii")
    image[22 * SECTOR:22 * SECTOR + len(cnf)] = cnf
    image[20 * SECTOR:20 * SECTOR + len(root)] = root
    parent = record(b"\x01", 20, 2 * SECTOR, 2)
    image[20 * SECTOR + len(root):20 * SECTOR + len(root) + len(parent)] = parent
    entry = record(b"SYSTEM.CNF;1", 22, len(cnf))
    start = 21 * SECTOR if second_sector else 20 * SECTOR + len(root) + len(parent)
    image[start:start + len(entry)] = entry
    path.write_bytes(image)
    return path


def make_bin(iso: Path, path: Path, mode: int = 1) -> Path:
    """Fixture raw estrutural: paridade EDC/ECC fica zerada intencionalmente."""
    with iso.open("rb") as source, path.open("wb") as output:
        index = 0
        while data := source.read(2048):
            if len(data) != 2048:
                raise ValueError("Fixture ISO deve ter setores completos")
            sector = bytearray(2352)
            sector[:12] = b"\0" + b"\xff" * 10 + b"\0"
            minutes, rest = divmod(index + 150, 75 * 60)
            seconds, frames = divmod(rest, 75)
            sector[12:15] = bytes((n // 10 * 16 + n % 10) for n in (minutes, seconds, frames))
            sector[15] = mode
            if mode == 1:
                sector[16:2064] = data
            else:
                sector[16:24] = b"\x00\x00\x08\x00" * 2
                sector[24:2072] = data
            output.write(sector)
            index += 1
    return path
