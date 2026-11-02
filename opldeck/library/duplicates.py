"""Diagnóstico por serial, sem comparar conteúdo nem modificar arquivos."""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from opldeck.core.formats import validate_game_id


@dataclass(frozen=True)
class GameOccurrence:
    game_id: str
    title: str
    kind: str
    path: Path
    record: int = 0


def duplicate_games(games):
    groups = defaultdict(list)
    seen = set()
    for game in games:
        try:
            validate_game_id(game.game_id)
        except ValueError:
            continue
        # Repeated references to an ISO are one occurrence. UL records sharing
        # an ID are separate entries, even within the same ul.cfg.
        identity = (game.kind, game.path.absolute(), game.record if game.kind == 'UL' else 0)
        if identity in seen:
            continue
        seen.add(identity)
        groups[game.game_id].append(game)
    return {serial: tuple(groups[serial]) for serial in sorted(groups) if len(groups[serial]) > 1}
