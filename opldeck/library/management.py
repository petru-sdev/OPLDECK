"""Gerenciamento UL com diário durável e recuperação explícita no Linux.

O índice decide a recuperação: bytes anteriores revertem movimentos; bytes novos
concluem a limpeza. Qualquer outro estado é preservado para diagnóstico manual.
Não há cópia das partes nem dependência de hard links (inclusive em FAT).
"""

import ctypes
from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import stat
import tempfile

from opldeck.core.conversion import (
    TRANSACTION_DIR, Progress, _cfg_bytes, _check_cancel, _free_space,
    _regular_reader, _sync_directory, directory_lock, require_clean_library,
)
from opldeck.core.formats import FormatError
from opldeck.core.ul import MAX_CFG_SIZE, RECORD_SIZE, parse_cfg

logger = logging.getLogger(__name__)
JOURNAL_LIMIT = MAX_CFG_SIZE * 4 + 128_000


def _move(source: Path, target: Path) -> None:
    """renameat2(RENAME_NOREPLACE): jamais substitui um destino existente."""
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        raise OSError("Este Linux não oferece renameat2 para movimentação sem sobrescrita.") from exc
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def _identity(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise FormatError(f"Esperado arquivo regular, sem link simbólico: {path}")
    # ctime muda ao renomear; mtime e identidade devem permanecer iguais.
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def _verify(path, identity):
    if identity is None:
        if os.path.lexists(path):
            raise FormatError(f"Parte antes ausente reapareceu; arquivo preservado: {path}")
        return
    if _identity(path) != identity:
        raise FormatError(f"Arquivo alterado externamente; preservado: {path}")


def _write(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_path(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _sync_directory(fd)
    finally:
        os.close(fd)


def _plan(before, game_id, title):
    entries = parse_cfg(before)
    matches = [(i, e) for i, e in enumerate(entries) if e.game_id == game_id]
    if len(matches) != 1:
        raise FormatError("ID não encontrado ou duplicado em ul.cfg.")
    index, old = matches[0]
    new = replace(old, title=title) if title is not None else None
    if new and any(i != index and e.title.casefold() == title.casefold() for i, e in enumerate(entries)):
        raise FormatError("Já existe um jogo UL com este título.")
    offset = index * RECORD_SIZE
    # Só os 32 bytes do título mudam. Padding do ID e campos reservados ficam intactos.
    record = title.encode("ascii").ljust(32, b"\0") + before[offset + 32:offset + 64] if new else b""
    after = before[:offset] + record + before[offset + RECORD_SIZE:]
    return old, new, after


def _actions(root, old, new):
    journal = root / TRANSACTION_DIR
    return [(root / old.part_name(i), root / new.part_name(i) if new else journal / f"part-{i:02x}")
            for i in range(old.parts)]


def management_summary(cfg_path, game_id):
    """Prévia somente leitura: arquivos exatos do registro, sem glob ou reparo."""
    cfg = Path(cfg_path).absolute()
    require_clean_library(cfg.parent)
    before = _cfg_bytes(cfg)
    if before is None:
        raise FormatError('ul.cfg não encontrado.')
    old, _, _ = _plan(before, game_id, None)
    present, missing, total = [], [], 0
    for index in range(old.parts):
        path = cfg.parent / old.part_name(index)
        try:
            identity = _identity(path)
        except FileNotFoundError:
            missing.append(path.name)
        else:
            present.append(path.name)
            total += identity[2]
    return old, present, missing, total


def _read_journal(root):
    journal = root / TRANSACTION_DIR
    if not stat.S_ISDIR(journal.lstat().st_mode):
        raise FormatError("Diário UL não é um diretório regular.")
    with _regular_reader(journal / "manifest.json") as stream:
        data = stream.read(JOURNAL_LIMIT + 1)
    try:
        if len(data) > JOURNAL_LIMIT:
            raise ValueError("diário excede o limite")
        doc = json.loads(data)
        if type(doc["version"]) is not int or doc["version"] not in (1, 2):
            raise ValueError("versão desconhecida")
        before = bytes.fromhex(doc["before"])
        old, new, after = _plan(before, doc["game_id"], doc["title"])
        if before == after or after.hex() != doc["after"]:
            raise ValueError("índice inconsistente")
        identities = doc["identities"]
        if len(identities) != old.parts or any(
            not (item is None and doc['version'] == 2) and (
                not isinstance(item, list) or len(item) != 4
                or any(type(n) is not int or n < 0 for n in item)) for item in identities
        ):
            raise ValueError("identidades inválidas")
        actions = _actions(root, old, new)
        allowed = {"manifest.json", "next.cfg"} | ({target.name for _, target in actions} if new is None else set())
        if any(path.name not in allowed for path in journal.iterdir()):
            raise ValueError("arquivos desconhecidos no diário")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise FormatError(f"Diário UL inválido; arquivos preservados: {exc}") from exc
    return before, after, old, new, actions, identities


def _clean_journal(root):
    journal = root / TRANSACTION_DIR
    # A ausência do manifesto só é recuperável em um diretório já vazio.
    (journal / "next.cfg").unlink(missing_ok=True)
    _sync_path(journal)
    (journal / "manifest.json").unlink()
    _sync_path(journal)
    journal.rmdir()
    _sync_path(root)


def _recover_locked(root):
    journal = root / TRANSACTION_DIR
    if not os.path.lexists(journal):
        return "Nenhuma operação UL pendente."
    if stat.S_ISDIR(journal.lstat().st_mode) and not any(journal.iterdir()):
        journal.rmdir()
        _sync_path(root)
        return "Limpeza da operação UL concluída."
    before, after, old, new, actions, identities = _read_journal(root)
    current = _cfg_bytes(root / "ul.cfg")
    if current not in (before, after):
        raise FormatError("ul.cfg foi alterado externamente; diário e partes preservados para análise.")
    committed = current == after
    # Valida todas as partes antes de iniciar a recuperação.
    for (source, target), identity in zip(actions, identities):
        if identity is None:
            _verify(source, None)
            _verify(target, None)
        elif source == target:
            _verify(source, identity)
        elif committed:
            if os.path.lexists(source):
                raise FormatError(f"Origem reapareceu após publicação; preservada: {source}")
            if new is not None or os.path.lexists(target):
                _verify(target, identity)
        else:
            source_exists, target_exists = os.path.lexists(source), os.path.lexists(target)
            if source_exists == target_exists:
                raise FormatError(f"Estado ambíguo das partes; arquivos preservados: {source.name}")
            _verify(source if source_exists else target, identity)
    # Torna durável o índice observado antes de apagar a quarentena.
    _sync_path(root)
    for (source, target), identity in reversed(list(zip(actions, identities))):
        if identity is None or source == target:
            continue
        if committed and new is None and os.path.lexists(target):
            _verify(target, identity)
            target.unlink()
        elif not committed and not os.path.lexists(source):
            _verify(target, identity)
            _move(target, source)
    _sync_path(root)
    _sync_path(journal)
    _clean_journal(root)
    result = "Operação UL concluída; limpeza finalizada." if committed else "Operação UL desfeita; estado anterior restaurado."
    logger.info("%s | ID=%s", result, old.game_id)
    return result


def recover_ul(directory, *, cancel=None, progress=None):
    """Resolve somente uma transação deste aplicativo; não reconstrói índices perdidos."""
    root = Path(directory).resolve(strict=True)
    _check_cancel(cancel)
    with directory_lock(root):
        return _recover_locked(root)


def _manage(cfg_path, game_id, title, *, cancel=None, progress=None, expected_index=None, expected_parts=None):
    cfg = Path(cfg_path).absolute()
    if cfg.name != "ul.cfg":
        raise FormatError("Gerenciamento requer o arquivo ul.cfg na raiz da biblioteca.")
    root = cfg.parent.resolve(strict=True)
    cfg = root / "ul.cfg"
    _check_cancel(cancel)
    with directory_lock(root) as directory_fd:
        require_clean_library(root)
        before = _cfg_bytes(cfg)
        if before is None:
            raise FormatError("ul.cfg não encontrado.")
        if expected_index is not None and before != expected_index:
            raise FormatError('A biblioteca mudou desde a confirmação. Revise a seleção novamente.')
        old, new, after = _plan(before, game_id, title)
        if new == old:
            return old
        actions = _actions(root, old, new)
        identities = []
        for i, (source, target) in enumerate(actions):
            try:
                identity = _identity(source)
            except FileNotFoundError:
                identity = None
            if source != target and os.path.lexists(target):
                raise FileExistsError(f"Destino já existe: {target}")
            identities.append(identity)
        if expected_parts is not None and tuple(tuple(item) if item is not None else None for item in identities) != expected_parts:
            raise FormatError('As partes do jogo mudaram desde a confirmação. Revise a seleção novamente.')
        manifest = json.dumps({"version": 2, "before": before.hex(), "after": after.hex(),
                               "game_id": game_id, "title": title, "identities": identities}).encode("utf-8")
        _free_space(root, len(manifest) + len(after) + 65536)
        journal = root / TRANSACTION_DIR
        # Publica um diário completo antes de mover qualquer parte.
        staging = Path(tempfile.mkdtemp(prefix=".opldeck-prepare-", dir=root))
        try:
            _write(staging / "manifest.json", manifest)
            _write(staging / "next.cfg", after)
            _sync_path(staging)
            _move(staging, journal)
        finally:
            if staging.exists():
                for path in staging.iterdir():
                    path.unlink()
                staging.rmdir()
        try:
            _sync_directory(directory_fd)
            logger.info("%s UL | ID=%s | título=%s | novo=%s | diário=%s",
                        "Renomear" if new else "Excluir", game_id, old.title, title, journal)
            total = sum(item[2] for item in identities if item is not None)
            done = 0
            for i, ((source, target), identity) in enumerate(zip(actions, identities)):
                _check_cancel(cancel)
                _verify(source, identity)
                if identity is None:
                    _verify(target, None)
                    logger.info('Parte %d/%d ausente; mantendo ausência: %s', i + 1, old.parts, source)
                elif source != target:
                    _move(source, target)
                done += identity[2] if identity is not None else 0
                logger.info("Parte %d/%d: %s → %s", i + 1, old.parts, source, target)
                if progress:
                    progress(Progress(done, total, i + 1, old.parts))
            _sync_path(journal)
            _sync_directory(directory_fd)
            _check_cancel(cancel)
            for (source, target), identity in zip(actions, identities):
                if identity is None:
                    _verify(source, None)
                _verify(target, identity)
            if _cfg_bytes(cfg) != before:
                raise FormatError("ul.cfg foi alterado por outro programa.")
            if _cfg_bytes(journal / "next.cfg") != after:
                raise FormatError("Índice preparado foi alterado externamente.")
            os.replace(journal / "next.cfg", cfg)
            _sync_directory(directory_fd)
            logger.info("Índice publicado: %s", cfg)
        except BaseException as exc:
            try:
                _recover_locked(root)
            except BaseException as recovery_error:
                exc.add_note(f"Recuperação pendente em {journal}: {recovery_error}. Execute recover-ul.")
            else:
                if _cfg_bytes(cfg) == after:
                    exc.add_note("A alteração já foi publicada em ul.cfg e foi mantida.")
            raise
        try:
            _recover_locked(root)
        except BaseException as exc:
            exc.add_note("A alteração já foi publicada. Execute recover-ul para concluir a limpeza.")
            raise
        return new or old


def rename_ul(cfg_path, game_id, title, *, cancel=None, progress=None):
    if not isinstance(title, str):
        raise FormatError("Informe o novo título UL.")
    return _manage(cfg_path, game_id, title, cancel=cancel, progress=progress)


def delete_ul(cfg_path, game_id, *, cancel=None, progress=None, expected_index=None, expected_parts=None):
    """Exclui o registro e as partes presentes, mesmo inválidas; exige confirmação na UI."""
    return _manage(cfg_path, game_id, None, cancel=cancel, progress=progress,
                   expected_index=expected_index, expected_parts=expected_parts)
