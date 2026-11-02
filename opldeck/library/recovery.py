"""Reconstrução de ul.cfg sem renomear partes nem adivinhar títulos pelo CRC."""

from bisect import bisect_right
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import uuid4

from opldeck.core.conversion import (
    Progress, _check_cancel, _free_space, _regular_reader, _remove_created,
    _snapshot, _sync_directory, _track, directory_lock, require_clean_library,
)
from opldeck.core.formats import CD_SIZE_THRESHOLD, FormatError, Media, PART_SIZE
from opldeck.core.iso import read_game_id_stream
from opldeck.library.management import _move
from opldeck.core.ul import MAX_CFG_SIZE, RECORD_SIZE, ULEntry, parse_cfg

logger = logging.getLogger(__name__)
PART_PATTERN = re.compile(r"ul\.([0-9a-f]{8})\.([a-z]{4}_[0-9]{3}\.[0-9]{2})\.([0-9a-f]{2})", re.I)
MAX_SCAN_FILES = 100_000


@dataclass(frozen=True)
class RecoveryGame:
    key: str
    game_id: str
    crc: int
    files: tuple[str, ...]
    size: int
    errors: tuple[str, ...]
    title: str = ""
    media: str = ""
    original_record: bytes | None = field(default=None, repr=False)

    @property
    def estimated_media(self):
        return "CD" if self.size < CD_SIZE_THRESHOLD else "DVD"


@dataclass(frozen=True)
class RecoveryScan:
    directory: Path
    fingerprint: str
    games: tuple[RecoveryGame, ...]
    warnings: tuple[str, ...]
    index_data: bytes | None = field(repr=False)
    inventory: tuple = field(repr=False)
    index_snapshot: tuple | None = field(repr=False)

    def to_plan(self):
        return {
            "version": 1, "directory": str(self.directory), "fingerprint": self.fingerprint,
            "index_exists": self.index_data is not None, "warnings": list(self.warnings),
            "instructions": "Informe o título ASCII cujo CRC coincide e confirme CD/DVD. include=false exclui o grupo do novo índice; não apaga partes.",
            "games": [{"key": g.key, "game_id": g.game_id, "crc": f"{g.crc:08X}",
                       "parts": len(g.files), "bytes": g.size, "files": list(g.files),
                       "errors": list(g.errors), "title": g.title, "media": g.media,
                       "estimated_media": g.estimated_media, "include": not g.errors}
                      for g in self.games],
        }


@dataclass(frozen=True)
class RebuildResult:
    cfg: Path
    games: int
    excluded: int
    backup: Path | None

    def __str__(self):
        return (f"{self.games} jogo(s) recuperado(s); {self.excluded} grupo(s) excluído(s) do índice. "
                f"Índice: {self.cfg}" + (f" | Backup: {self.backup}" if self.backup else ""))


class _PartsReader:
    """Fluxo lógico usado apenas para ler os metadados ISO9660 entre partes."""

    def __init__(self, streams, sizes):
        self.streams = streams
        self.offsets = [0]
        for size in sizes:
            self.offsets.append(self.offsets[-1] + size)
        self.position = 0

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        bases = {0: 0, 1: self.position, 2: self.offsets[-1]}
        if whence not in bases or bases[whence] + offset < 0:
            raise ValueError("Posição inválida na imagem UL.")
        self.position = bases[whence] + offset
        return self.position

    def read(self, length=-1):
        remaining = max(0, self.offsets[-1] - self.position)
        length = remaining if length < 0 else min(length, remaining)
        result = bytearray()
        while length:
            part = bisect_right(self.offsets, self.position) - 1
            stream = self.streams[part]
            stream.seek(self.position - self.offsets[part])
            data = stream.read(min(length, self.offsets[part + 1] - self.position))
            if not data:
                raise FormatError("Parte truncada durante a identificação.")
            result.extend(data)
            self.position += len(data)
            length -= len(data)
        return bytes(result)


def _read_index(path):
    try:
        with _regular_reader(path) as stream:
            before = _snapshot(os.fstat(stream.fileno()))
            data = stream.read(MAX_CFG_SIZE + 1)
            if before != _snapshot(os.fstat(stream.fileno())):
                raise FormatError("ul.cfg mudou durante a leitura; faça nova análise.")
    except FileNotFoundError:
        return None, None
    if len(data) > MAX_CFG_SIZE:
        raise FormatError("ul.cfg excede o limite de leitura para recuperação.")
    return data, before


def _inventory(root):
    result = []
    with os.scandir(root) as listing:
        for item in listing:
            name = item.name
            if not name.lower().startswith("ul.") or name == "ul.cfg" or name.startswith("ul.cfg.backup-"):
                continue
            info = item.stat(follow_symlinks=False)
            result.append((name, info.st_mode, _snapshot(info)))
            if len(result) > MAX_SCAN_FILES:
                raise FormatError("A análise excedeu o limite de 100.000 arquivos UL.")
    return tuple(sorted(result))


def _fingerprint(root, inventory, index_data, index_snapshot):
    data = json.dumps([str(root), inventory, index_snapshot,
                       None if index_data is None else hashlib.sha256(index_data).hexdigest()],
                      separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def _scan_locked(root, cancel=None):
    require_clean_library(root)
    previous, previous_snapshot = _read_index(root / "ul.cfg")
    inventory = _inventory(root)
    warnings, hints, groups = [], {}, {}
    if previous is not None:
        if len(previous) % RECORD_SIZE:
            warnings.append("ul.cfg contém um registro final truncado; somente registros completos serão usados como referência.")
        for offset in range(0, len(previous) - RECORD_SIZE + 1, RECORD_SIZE):
            record = previous[offset:offset + RECORD_SIZE]
            try:
                entry = parse_cfg(record)[0]
                hints.setdefault((entry.crc, entry.game_id), []).append((entry, record))
            except FormatError as exc:
                warnings.append(f"Registro antigo {offset // RECORD_SIZE + 1} ignorado: {exc}")
    for name, mode, snapshot in inventory:
        _check_cancel(cancel)
        match = PART_PATTERN.fullmatch(name)
        if not match:
            warnings.append(f"Nome UL não reconhecido; arquivo preservado e ignorado: {name}")
            continue
        crc, game_id, index = int(match[1], 16), match[2].upper(), int(match[3], 16)
        groups.setdefault((crc, game_id), []).append((index, name, mode, snapshot))
    for key, entries in hints.items():
        if key not in groups:
            for entry, _ in entries:
                warnings.append(f"Registro anterior sem partes encontradas: {entry.game_id} / {entry.title}; ficará fora do novo índice.")
    games = []
    for (crc, game_id), parts in sorted(groups.items()):
        _check_cancel(cancel)
        parts.sort()
        errors = []
        indices = [p[0] for p in parts]
        if len(parts) > 255 or indices != list(range(len(parts))):
            errors.append("Sequência incompleta/duplicada: esperado 00 até a última parte, no máximo fe.")
        for position, (index, name, mode, snapshot) in enumerate(parts):
            if not stat.S_ISREG(mode):
                errors.append(f"Não é arquivo regular (links não aceitos): {name}")
            if name != f"ul.{crc:08X}.{game_id}.{index:02x}":
                errors.append(f"Nome fora do padrão de maiúsculas/minúsculas: {name}")
            size = snapshot[2]
            if (not 0 < size <= PART_SIZE or size % 2048
                    or (position < len(parts) - 1 and size != PART_SIZE)):
                errors.append(f"Tamanho inválido ou desalinhado a setores: {name}")
        if not errors:
            try:
                with ExitStack() as stack:
                    streams = [stack.enter_context(_regular_reader(root / p[1])) for p in parts]
                    for stream, part in zip(streams, parts):
                        if _snapshot(os.fstat(stream.fileno())) != part[3]:
                            raise FormatError("Parte alterada durante a análise.")
                    internal_id = read_game_id_stream(_PartsReader(streams, [p[3][2] for p in parts]))
                    if internal_id != game_id:
                        raise FormatError(f"ID interno {internal_id} difere do nome {game_id}.")
                    for stream, part in zip(streams, parts):
                        if _snapshot(os.fstat(stream.fileno())) != part[3]:
                            raise FormatError("Parte alterada durante a identificação.")
            except (OSError, FormatError) as exc:
                errors.append(str(exc))
        title, media, record = "", "", None
        candidates = hints.get((crc, game_id), [])
        if len(candidates) == 1:
            entry, old_record = candidates[0]
            title, media = entry.title, entry.media.name
            if entry.parts == len(parts):
                record = old_record
        elif candidates:
            warnings.append(f"{game_id}/{crc:08X}: registros antigos ambíguos; confirme título e mídia manualmente.")
        games.append(RecoveryGame(f"{crc:08X}:{game_id}", game_id, crc, tuple(p[1] for p in parts),
                                  sum(p[3][2] for p in parts), tuple(errors), title, media, record))
    if inventory != _inventory(root) or (previous, previous_snapshot) != _read_index(root / "ul.cfg"):
        raise FormatError("Biblioteca alterada durante a análise; faça nova análise.")
    for game_id, count in sorted(Counter(g.game_id for g in games).items()):
        if count > 1:
            warnings.append(f"ID {game_id} aparece em mais de um grupo; selecione apenas um deles.")
    fingerprint = _fingerprint(root, inventory, previous, previous_snapshot)
    logger.info("Análise de recuperação: %s | grupos=%d | avisos=%d | índice existente=%s",
                root, len(games), len(warnings), previous is not None)
    return RecoveryScan(root, fingerprint, tuple(games), tuple(warnings), previous, inventory, previous_snapshot)


def scan_ul(directory, *, cancel=None):
    root = Path(directory).resolve(strict=True)
    _check_cancel(cancel)
    with directory_lock(root):
        return _scan_locked(root, cancel)


def record_for_game(game, title, media):
    if game.errors:
        raise FormatError(f"{game.key}: " + "; ".join(game.errors))
    if not isinstance(title, str) or not isinstance(media, str) or media not in ("CD", "DVD"):
        raise FormatError(f"{game.key}: informe o título original e confirme a mídia CD/DVD.")
    entry = ULEntry(title, game.game_id, len(game.files), Media[media])
    if entry.crc != game.crc:
        raise FormatError(f"{game.key}: CRC do título ({entry.crc:08X}) não corresponde às partes ({game.crc:08X}).")
    if game.original_record and title == game.title and media == game.media:
        return game.original_record
    return entry.to_bytes()


def build_records(scan, choices):
    """Valida a escolha explícita; nenhuma alteração em disco."""
    candidates = {g.key: g for g in scan.games}
    seen_keys, seen_ids, seen_titles, records = set(), set(), set(), []
    for choice in choices:
        if not isinstance(choice, dict) or type(choice.get("include")) is not bool:
            raise FormatError("Cada jogo do plano precisa de include=true ou false.")
        key = choice.get("key")
        if not isinstance(key, str) or key not in candidates or key in seen_keys:
            raise FormatError("Grupo desconhecido ou duplicado no plano de recuperação.")
        seen_keys.add(key)
        if not choice["include"]:
            continue
        game = candidates[key]
        title, media = choice.get("title"), choice.get("media")
        record = record_for_game(game, title, media)
        if game.game_id in seen_ids or title.casefold() in seen_titles:
            raise FormatError("ID ou título duplicado entre os jogos selecionados.")
        seen_ids.add(game.game_id)
        seen_titles.add(title.casefold())
        records.append(record)
    if not records:
        raise FormatError("Selecione ao menos um jogo válido para reconstruir o índice.")
    if len(records) * RECORD_SIZE > MAX_CFG_SIZE:
        raise FormatError("Índice reconstruído excede o limite suportado.")
    return b"".join(records)


def choices_from_plan(scan, plan):
    if (not isinstance(plan, dict) or plan.get("version") != 1
            or plan.get("fingerprint") != scan.fingerprint):
        raise FormatError("Plano inválido ou biblioteca alterada desde a análise; gere um novo plano.")
    choices = plan.get("games")
    if not isinstance(choices, list):
        raise FormatError("Plano precisa conter uma lista games.")
    build_records(scan, choices)
    return choices


def rebuild_ul(scan, choices, *, replace_existing=False, cancel=None, progress=None):
    """Publica ul.cfg; exige opção explícita e backup quando um índice já existe."""
    root, cfg = scan.directory, scan.directory / "ul.cfg"
    _check_cancel(cancel)
    updated = build_records(scan, choices)
    count = len(updated) // RECORD_SIZE
    if scan.index_data is not None and not replace_existing:
        raise FileExistsError("ul.cfg já existe. Confirme sua substituição com backup para continuar.")
    created, backup, committed = [], None, False
    with directory_lock(root) as directory_fd:
        require_clean_library(root)
        current = _scan_locked(root, cancel)
        if current.fingerprint != scan.fingerprint:
            raise FormatError("Biblioteca alterada desde a prévia; faça nova análise antes de gravar.")
        updated = build_records(current, choices)
        _free_space(root, len(updated) + len(scan.index_data or b"") + 65536)
        try:
            with tempfile.NamedTemporaryFile("wb", prefix=".opldeck-rebuild-", dir=root, delete=False) as stream:
                temporary = Path(stream.name)
                _track(temporary, stream, created)
                for index in range(count):
                    _check_cancel(cancel)
                    if stream.write(updated[index * RECORD_SIZE:(index + 1) * RECORD_SIZE]) != RECORD_SIZE:
                        raise OSError("Gravação incompleta do índice reconstruído.")
                    if progress:
                        progress(Progress(index + 1, count, index + 1, count))
                stream.flush()
                os.fsync(stream.fileno())
                prepared_snapshot = _snapshot(os.fstat(stream.fileno()))
            if scan.index_data is not None:
                with tempfile.NamedTemporaryFile("wb", prefix=".opldeck-backup-", dir=root, delete=False) as stream:
                    backup_temp = Path(stream.name)
                    _track(backup_temp, stream, created)
                    if stream.write(scan.index_data) != len(scan.index_data):
                        raise OSError("Gravação incompleta do backup.")
                    stream.flush()
                    os.fsync(stream.fileno())
                    backup_snapshot = _snapshot(os.fstat(stream.fileno()))
                if _read_index(backup_temp) != (scan.index_data, backup_snapshot):
                    raise FormatError("Backup preparado foi alterado externamente; publicação cancelada.")
                backup = root / f"ul.cfg.backup-{uuid4().hex}"
                _move(backup_temp, backup)
                _sync_directory(directory_fd)
                logger.info("Backup do índice anterior: %s | bytes=%d", backup, len(scan.index_data))
            _check_cancel(cancel)
            if (scan.inventory != _inventory(root)
                    or (scan.index_data, scan.index_snapshot) != _read_index(cfg)):
                raise FormatError("Biblioteca alterada antes da publicação; arquivos externos preservados.")
            if _read_index(temporary) != (updated, prepared_snapshot):
                raise FormatError("Índice preparado foi alterado externamente; publicação cancelada.")
            # O índice novo está completo antes de se tornar visível.
            if scan.index_data is None:
                _move(temporary, cfg)
            else:
                os.replace(temporary, cfg)
            committed = True
            _sync_directory(directory_fd)
        except BaseException as exc:
            _remove_created(created, exc)
            if committed:
                exc.add_note("O índice reconstruído já foi publicado; foi preservado.")
            if backup is not None and backup.exists():
                exc.add_note(f"Backup preservado: {backup}")
            raise
    result = RebuildResult(cfg, count, len(scan.games) - count, backup)
    logger.info("Reconstrução UL concluída: %s", result)
    return result
