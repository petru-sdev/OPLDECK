"""Publicação sequencial das prévias aprovadas, com guardas por capa."""

from dataclasses import dataclass
from pathlib import Path

from opldeck.covers.artwork import CoverState, decode_image, image_bytes, load_cover, save_cover
from opldeck.covers.cover_preload import CoverKey
from opldeck.library.devices import require_volume


@dataclass(frozen=True)
class CoverSaveJob:
    key: CoverKey
    preview: Path
    state: CoverState
    existing: bool = False


@dataclass(frozen=True)
class CoverSaveResult:
    outcomes: tuple
    cancelled: bool


def save_covers(jobs, *, preserve=True, volume=None, cancel=None, progress=None):
    outcomes = []
    seen = set()
    for index, job in enumerate(jobs):
        if cancel and cancel.is_set():
            break
        key = job.key
        identity = (key.root, key.game_id)
        if identity in seen:
            status, detail = 'Ignorada', 'Este destino já apareceu no lote.'
        elif preserve and job.existing:
            status, detail = 'Preservada', 'Capa existente mantida.'
        else:
            try:
                if volume and key.root == volume.root:
                    require_volume(volume, write=True)
                # A legacy cover can appear after preparation without changing
                # the canonical target guard. Preserve it too when requested.
                if preserve and load_cover(key.root, key.game_id, title=key.title,
                                           filename=key.filename).path is not None:
                    outcomes.append((index, 'Preservada', 'Capa encontrada após a prévia; mantida.'))
                    seen.add(identity)
                    if progress:
                        progress(outcomes[-1])
                    continue
                image = decode_image(image_bytes(job.preview))
                if cancel and cancel.is_set():
                    break
                path = save_cover(key.root, key.game_id, image, job.state)
                status, detail = 'Salva', str(path)
            except (OSError, ValueError) as exc:
                status, detail = 'Falhou', '\n'.join([str(exc), *getattr(exc, '__notes__', [])])
        seen.add(identity)
        outcomes.append((index, status, detail))
        if progress:
            progress(outcomes[-1])
    return CoverSaveResult(tuple(outcomes), bool(cancel and cancel.is_set()))
