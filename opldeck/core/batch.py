"""Fila sequencial de conversões com progresso por jogo e por bytes do lote."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import stat

from opldeck.core.binimage import convert_bin, inspect_bin
from opldeck.core.conversion import (
    CancelledError, Progress, _cfg_bytes, _check_cancel, _free_space,
    export_ul, install_iso, require_clean_library,
)
from opldeck.core.formats import FormatError, Media, PART_SIZE, part_count, validate_game_id
from opldeck.core.iso import inspect_iso
from opldeck.core.ul import ULEntry, parse_cfg

OPERATIONS = {"install": "ISO → UL", "export": "UL → ISO", "bin": "BIN → ISO"}
logger = logging.getLogger("opldeck")


@dataclass(frozen=True)
class TransferJob:
    kind: str
    source: Path
    destination: Path
    game_id: str
    title: str
    size: int
    media: Media | None = None

    @property
    def output_directory(self):
        return self.destination if self.kind == "install" else self.destination.parent


@dataclass(frozen=True)
class BatchProgress:
    index: int
    count: int
    title: str
    operation: str
    current: Progress
    overall_completed: int
    overall_total: int
    phase: str

    @property
    def completed(self):
        return self.current.completed

    @property
    def total(self):
        return self.current.total


@dataclass(frozen=True)
class BatchResult:
    jobs: tuple[TransferJob, ...]
    completed: tuple
    attempted: int
    error: str = ""
    cancelled: bool = False

    @property
    def pending(self):
        return len(self.jobs) - self.attempted


def validate_jobs(jobs):
    """Validação em memória, usada também pela prévia, sem leitura de disco."""
    if not jobs:
        raise FormatError("Selecione pelo menos um jogo.")
    ids, titles, targets = set(), set(), set()
    for job in jobs:
        if job.kind not in OPERATIONS or job.kind != jobs[0].kind:
            raise FormatError("Selecione uma única modalidade de transferência por lote.")
        if job.output_directory.absolute() != jobs[0].output_directory.absolute():
            raise FormatError("Escolha uma única pasta de destino por lote.")
        validate_game_id(job.game_id)
        if job.size <= 0:
            raise FormatError(f"Tamanho inválido: {job.title}")
        if job.game_id in ids:
            raise FormatError(f"ID repetido na seleção: {job.game_id}")
        ids.add(job.game_id)
        if job.kind == "install":
            ULEntry(job.title, job.game_id, part_count(job.size), job.media)
            if job.title.casefold() in titles:
                raise FormatError(f"Título UL repetido: {job.title}")
            titles.add(job.title.casefold())
        else:
            if job.destination.suffix.lower() != ".iso":
                raise FormatError("O destino deve ser um novo arquivo .iso.")
            target = str(job.destination.absolute()).casefold()
            if target in targets:
                raise FormatError("Há destinos repetidos na fila.")
            targets.add(target)


def check_source(job):
    if job.kind in {"install", "bin"}:
        game = inspect_iso(job.source) if job.kind == "install" else inspect_bin(job.source)
        size = game.size if job.kind == "install" else game.iso_size
        if game.game_id != job.game_id or size != job.size:
            raise FormatError(f"Origem alterada desde a seleção; atualize a lista: {job.source}")
    else:
        require_clean_library(job.source.parent)
        matches = [entry for entry in parse_cfg(_cfg_bytes(job.source) or b"") if entry.game_id == job.game_id]
        if len(matches) != 1:
            raise FormatError(f"ID ausente ou duplicado no índice: {job.game_id}")
        entry = matches[0]
        size = 0
        for index in range(entry.parts):
            path = job.source.parent / entry.part_name(index)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= PART_SIZE or (
                index < entry.parts - 1 and info.st_size != PART_SIZE
            ):
                raise FormatError(f"Parte UL inválida: {path}")
            size += info.st_size
        if size != job.size:
            raise FormatError(f"Tamanho UL alterado desde a seleção: {job.game_id}")


def preflight(jobs, cancel=None):
    validate_jobs(jobs)
    needed = {}
    for job in jobs:
        _check_cancel(cancel)
        check_source(job)
        root = job.output_directory.resolve(strict=True)
        if not root.is_dir():
            raise FormatError(f"Destino não é uma pasta: {root}")
        require_clean_library(root)
        needed[root] = needed.get(root, 0) + job.size
        if job.kind == "install":
            entries = parse_cfg(_cfg_bytes(root / "ul.cfg") or b"")
            if any(e.game_id == job.game_id or e.title.casefold() == job.title.casefold() for e in entries):
                raise FormatError(f"O destino já contém o ID ou título de {job.title} ({job.game_id}).")
            entry = ULEntry(job.title, job.game_id, part_count(job.size), job.media)
            if any(os.path.lexists(root / entry.part_name(i)) for i in range(entry.parts)):
                raise FormatError(f"Já existem partes de {job.title} no destino.")
        elif os.path.lexists(job.destination):
            raise FileExistsError(f"Destino já existe: {job.destination}")
    for root, size in needed.items():
        _free_space(root, size + (len(jobs) * 64 + 65536))


def run_batch(jobs, *, cancel=None, progress=None):
    """Concluídos são preservados; falha/cancelamento interrompe a fila."""
    jobs = tuple(jobs)
    completed = []
    attempted = 0
    done = 0
    total = sum(job.size for job in jobs)
    try:
        preflight(jobs, cancel)
        for index, job in enumerate(jobs, 1):
            _check_cancel(cancel)
            attempted = index
            operation = OPERATIONS[job.kind]

            def report(event, phase=None):
                if progress:
                    progress(BatchProgress(index, len(jobs), job.title, operation, event,
                                           done + min(event.completed, job.size), total, phase or event.phase))

            report(Progress(0, job.size, 0, 0), "preparing")
            check_source(job)
            logger.info("Lote · jogo %d de %d · %s · ID=%s · destino=%s",
                        index, len(jobs), job.title, job.game_id, job.destination)
            if job.kind == "install":
                result = install_iso(job.source, job.destination, title=job.title, media=job.media,
                                     cancel=cancel, progress=report)
            elif job.kind == "export":
                result = export_ul(job.source, job.game_id, job.destination, cancel=cancel, progress=report)
            else:
                result = convert_bin(job.source, job.destination, cancel=cancel, progress=report)
            completed.append(result)
            parts = (job.size + PART_SIZE - 1) // PART_SIZE
            report(Progress(job.size, job.size, parts, parts), "complete")
            done += job.size
            logger.info("Lote · jogo %d de %d concluído · %s", index, len(jobs), job.title)
    except Exception as exc:
        error = "\n".join([str(exc), *getattr(exc, "__notes__", [])])
        if attempted > len(completed):
            job = jobs[attempted - 1]
            error = f"Jogo {attempted} de {len(jobs)} · {job.title} · {job.game_id}\n{error}"
        return BatchResult(jobs, tuple(completed), attempted, error, isinstance(exc, CancelledError))
    return BatchResult(jobs, tuple(completed), attempted)
