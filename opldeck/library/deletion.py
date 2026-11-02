"""Exclusão sequencial de uma seleção confirmada, com diário por jogo."""

from dataclasses import dataclass, field
from pathlib import Path

from opldeck.core.conversion import CancelledError, Progress, _cfg_bytes, _check_cancel, require_clean_library
from opldeck.core.formats import FormatError
from opldeck.library.management import _identity, _plan, delete_ul
from opldeck.core.ul import ULEntry, parse_cfg


@dataclass(frozen=True)
class DeleteGame:
    entry: ULEntry
    identities: tuple

    @property
    def present(self):
        return tuple(self.entry.part_name(i) for i, value in enumerate(self.identities) if value is not None)

    @property
    def missing(self):
        return tuple(self.entry.part_name(i) for i, value in enumerate(self.identities) if value is None)

    @property
    def size(self):
        return sum(value[2] for value in self.identities if value is not None)


@dataclass(frozen=True)
class DeletePlan:
    cfg: Path
    before: bytes = field(repr=False)
    games: tuple[DeleteGame, ...] = field(repr=False)


@dataclass(frozen=True)
class DeleteProgress:
    index: int
    count: int
    title: str
    current: Progress

    @property
    def completed(self):
        return self.current.completed

    @property
    def total(self):
        return self.current.total


@dataclass(frozen=True)
class DeleteResult:
    plan: DeletePlan
    removed: tuple[str, ...]
    error: str = ''
    cancelled: bool = False


def prepare_delete(cfg_path, game_ids):
    cfg = Path(cfg_path).absolute()
    if cfg.name != 'ul.cfg':
        raise FormatError('Gerenciamento requer o arquivo ul.cfg na raiz da biblioteca.')
    ids = tuple(game_ids)
    if not ids or len(set(ids)) != len(ids):
        raise FormatError('Selecione jogos com IDs únicos para excluir.')
    require_clean_library(cfg.parent)
    before = _cfg_bytes(cfg)
    if before is None:
        raise FormatError('ul.cfg não encontrado.')
    entries = {}
    for entry in parse_cfg(before):
        entries.setdefault(entry.game_id, []).append(entry)
    games = []
    for game_id in ids:
        matches = entries.get(game_id, ())
        if len(matches) != 1:
            raise FormatError(f'ID ausente ou duplicado em ul.cfg: {game_id}')
        entry = matches[0]
        identities = []
        for index in range(entry.parts):
            try:
                value = tuple(_identity(cfg.parent / entry.part_name(index)))
            except FileNotFoundError:
                value = None
            identities.append(value)
        games.append(DeleteGame(entry, tuple(identities)))
    if _cfg_bytes(cfg) != before:
        raise FormatError('A biblioteca mudou durante a leitura. Revise a seleção novamente.')
    return DeletePlan(cfg, before, tuple(games))


def delete_selection(plan, *, cancel=None, progress=None):
    removed = []
    before = plan.before
    active = None
    try:
        _check_cancel(cancel)
        # Valida toda a seleção antes de remover o primeiro jogo.
        if prepare_delete(plan.cfg, (game.entry.game_id for game in plan.games)) != plan:
            raise FormatError('A biblioteca ou suas partes mudaram desde a confirmação. Revise a seleção novamente.')
        for index, game in enumerate(plan.games, 1):
            active = (index, game.entry)
            _check_cancel(cancel)
            def report(event):
                if progress:
                    progress(DeleteProgress(index, len(plan.games), game.entry.title, event))
            report(Progress(0, game.size, 0, game.entry.parts))
            _, _, after = _plan(before, game.entry.game_id, None)
            try:
                delete_ul(plan.cfg, game.entry.game_id, cancel=cancel, progress=report,
                          expected_index=before, expected_parts=game.identities)
            except Exception:
                # Um erro de limpeza após publicar o índice não desfaz a exclusão.
                try:
                    if _cfg_bytes(plan.cfg) == after:
                        removed.append(game.entry.game_id)
                except (OSError, ValueError):
                    pass
                raise
            removed.append(game.entry.game_id)
            before = after
    except Exception as exc:
        message = '\n'.join([str(exc), *getattr(exc, '__notes__', [])])
        if isinstance(exc, CancelledError):
            message = 'Exclusão cancelada. Jogos já excluídos permanecem excluídos.'
        if active:
            index, entry = active
            message = f'Jogo {index} de {len(plan.games)} · {entry.title} ({entry.game_id})\n' + message
        return DeleteResult(plan, tuple(removed), message, isinstance(exc, CancelledError))
    return DeleteResult(plan, tuple(removed))
