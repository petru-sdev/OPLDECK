"""Preparação silenciosa de capas em memória; nunca grava na biblioteca."""

from collections import OrderedDict
from dataclasses import dataclass, replace
import logging
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from opldeck.covers.artwork import LoadedCover, load_cover
from opldeck.covers.cover_network import CoverFetcher
from opldeck.library.devices import require_volume
from opldeck.core.formats import validate_game_id
from opldeck.ui.preferences import application_settings
from opldeck.covers.providers import ProviderPreferences

CACHE_BYTES = 64 * 1024 * 1024
CACHE_ENTRIES = 256
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CoverKey:
    root: Path
    game_id: str
    title: str = ''
    filename: str = ''

    @classmethod
    def make(cls, root, game_id, title='', filename=''):
        validate_game_id(game_id)
        return cls(Path(root).absolute(), game_id, title, filename)


@dataclass(frozen=True)
class PreparedCover:
    local: LoadedCover
    download: object = None
    pending: bool = False
    error: str = ''
    not_found: bool = False
    remaining: tuple[str, ...] = ()
    attempted: tuple[str, ...] = ()

    @property
    def size(self):
        return sum(image.sizeInBytes() for image in
                   (self.local.image, self.download.image if self.download else None) if image is not None)


class LoadSignals(QObject):
    finished = Signal(object, object)


class LoadCover(QRunnable):
    def __init__(self, token, volume):
        super().__init__()
        self.token = token
        self.volume = volume
        self.cancelled = Event()
        self.signals = LoadSignals()

    def run(self):
        key = self.token[2]
        result = None
        try:
            if not self.cancelled.is_set():
                if self.volume and key.root == self.volume.root:
                    require_volume(self.volume)
                result = load_cover(key.root, key.game_id, title=key.title, filename=key.filename)
                if self.volume and key.root == self.volume.root:
                    require_volume(self.volume)
        except (OSError, ValueError) as exc:
            result = LoadedCover(None, None, None, str(exc))
        finally:
            self.signals.finished.emit(self.token, None if self.cancelled.is_set() else result)


class CoverPreloader(QObject):
    ready = Signal(object, object)
    invalidated = Signal(str)
    preferences_changed = Signal()
    missing = Signal(object)

    def __init__(self, parent=None, *, settings=None, fetcher=None, max_bytes=CACHE_BYTES,
                 max_entries=CACHE_ENTRIES):
        super().__init__(parent)
        self.settings = settings if settings is not None else application_settings()
        self.fetcher = fetcher if fetcher is not None else CoverFetcher(self)
        self.fetcher.finished.connect(self._downloaded)
        self.pool = QThreadPool.globalInstance()
        self.cache = OrderedDict()
        self.local_queue = OrderedDict()
        self.network_queue = OrderedDict()
        self.revisions = {}
        self.generation = 0
        self.volume = None
        self.worker = None
        self.network_token = None
        self.network_paused = False
        self.paused = False
        self.awaiting_choice = False
        self.closed = False
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.preferences = ProviderPreferences.load(self.settings)
        self.fetcher.providers = self.preferences.catalog
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._pump)

    @property
    def busy(self):
        return bool(self.worker or self.network_token or self.local_queue or self.network_queue or self.awaiting_choice)

    @property
    def cache_bytes(self):
        return sum(item.size for item in self.cache.values())

    def reset(self, volume=None, *, reason='library'):
        self.generation += 1
        self.volume = volume
        self.timer.stop()
        self.cache.clear()
        self.revisions.clear()
        self.local_queue.clear()
        self.network_queue.clear()
        self.network_token = None
        self.fetcher.cancel()
        if self.worker:
            self.worker.cancelled.set()
        self.network_paused = False
        self.awaiting_choice = False
        self.invalidated.emit(reason)

    def close(self):
        self.closed = True
        self.reset()

    def sync_preferences(self):
        self.settings.sync()
        preferences = ProviderPreferences.load(self.settings)
        if preferences != self.preferences:
            self.preferences = preferences
            self.reset(self.volume, reason='provider')
            self.fetcher.providers = preferences.catalog
            self.preferences_changed.emit()
            return True
        return False

    def set_paused(self, paused):
        self.paused = paused
        if paused and self.network_token:
            token = self.network_token
            self.network_token = None
            self.network_queue[token[2]] = token
            self.network_queue.move_to_end(token[2], last=False)
            self.fetcher.cancel()
        if not paused:
            self._schedule()

    def peek(self, key):
        entry = self.cache.get(key)
        if entry is not None:
            self.cache.move_to_end(key)
        return entry

    def request(self, key, *, priority=True, force=False):
        if self.closed:
            return None
        if force:
            self.invalidate_game(key.root, key.game_id)
        cached = self.peek(key)
        if cached is None:
            token = (self.generation, self.revisions.get(key, 0), key)
            if not self.worker or self.worker.token != token:
                self.local_queue[key] = token
                if priority:
                    self.local_queue.move_to_end(key, last=False)
        elif priority and key in self.network_queue:
            self.network_queue.move_to_end(key, last=False)
        self._schedule()
        return cached

    def preload(self, keys):
        self.sync_preferences()
        for key in keys:
            self.request(key, priority=False)

    def try_alternatives(self, keys, provider_ids):
        if self.closed or not self.preferences.enabled or self.network_paused:
            return
        for key in keys:
            entry = self.cache.get(key)
            if entry is None or not entry.not_found or entry.pending:
                continue
            remaining = tuple(dict.fromkeys(provider_id for provider_id in provider_ids
                                             if provider_id in self.preferences.catalog
                                             and provider_id not in entry.attempted
                                             and provider_id != self.preferences.provider_id))
            if not remaining:
                continue
            self.network_queue[key] = (self.generation, self.revisions.get(key, 0), key)
            self._store(key, replace(entry, remaining=remaining, pending=True, not_found=False, error=''))
        self._schedule()

    def invalidate_game(self, root, game_id):
        keys = set(self.cache) | set(self.local_queue) | set(self.network_queue)
        if self.worker:
            keys.add(self.worker.token[2])
        if self.network_token:
            keys.add(self.network_token[2])
        for key in keys:
            if key.root == root and key.game_id == game_id:
                self.revisions[key] = self.revisions.get(key, 0) + 1
                self.cache.pop(key, None)
                self.local_queue.pop(key, None)
                self.network_queue.pop(key, None)
                if self.network_token and self.network_token[2] == key:
                    self.network_token = None
                    self.fetcher.cancel()

    def _schedule(self):
        if not self.closed and not self.timer.isActive():
            self.timer.start(0)

    def _current(self, token):
        return (not self.closed and token[0] == self.generation and
                token[1] == self.revisions.get(token[2], 0))

    def _store(self, key, entry):
        self.cache.pop(key, None)
        if entry.size <= self.max_bytes:
            self.cache[key] = entry
        while self.cache and (len(self.cache) > self.max_entries or self.cache_bytes > self.max_bytes):
            # Pending entries contain no decoded image. Keep their save guards
            # until the download completes, even when a large local image arrives.
            old = next((candidate for candidate, item in self.cache.items() if not item.pending),
                       next(iter(self.cache)))
            self.cache.pop(old)
            self.network_queue.pop(old, None)
        self.ready.emit(key, entry)

    @Slot()
    def _pump(self):
        if self.closed or self.paused or self.awaiting_choice:
            return
        pending = sum(item.pending for item in self.cache.values())
        if self.worker is None and self.local_queue and pending < min(8, self.max_entries):
            _, token = self.local_queue.popitem(last=False)
            self.worker = LoadCover(token, self.volume)
            self.worker.signals.finished.connect(self._loaded)
            self.pool.start(self.worker)
        if self.network_token is None and self.network_queue and not self.network_paused:
            key, token = self.network_queue.popitem(last=False)
            if self._current(token) and key in self.cache:
                self.network_token = token
                self.fetcher.start(self.cache[key].remaining[0], key.game_id)
            else:
                self._schedule()

    @Slot(object, object)
    def _loaded(self, token, loaded):
        self.worker = None
        if self._current(token) and loaded is not None:
            key = token[2]
            pending = (loaded.image is None and loaded.state is not None and
                       self.preferences.enabled and not self.network_paused)
            previous = next((item for other, item in self.cache.items()
                               if other.game_id == key.game_id and item.download), None) if pending else None
            downloaded = previous.download if previous else None
            pending = pending and downloaded is None
            if pending:
                self.network_queue[key] = token
            self._store(key, PreparedCover(loaded, downloaded, pending,
                                          remaining=(self.preferences.provider_id,) if pending else (),
                                          attempted=previous.attempted if previous else ()))
        self._schedule()

    @Slot(object, str)
    def _downloaded(self, result, error):
        token = self.network_token
        self.network_token = None
        if token is None or not self._current(token):
            return
        key = token[2]
        provider_id = self.fetcher.provider_id
        try:
            if self.volume and key.root == self.volume.root:
                require_volume(self.volume)
        except (OSError, ValueError):
            self.reset(self.volume)
            return
        # A missing serial is normal; connection/rate-limit failures stop this
        # background run instead of trying every game against a failing host.
        stop = bool(error and self.fetcher.last_http_status not in (200, 404))
        if stop:
            self.network_paused = True
            self.network_queue.clear()
            logger.warning('Pré-carregamento online suspenso: %s', error)
        missing = []
        for other, entry in list(self.cache.items()):
            matches = (other.game_id == key.game_id and entry.remaining and entry.remaining[0] == provider_id)
            if entry.pending and (matches or stop):
                self.network_queue.pop(other, None)
                attempted = (*entry.attempted, provider_id) if matches else entry.attempted
                remaining = entry.remaining[1:] if matches else ()
                not_found = bool(error and self.fetcher.last_http_status == 404 and matches)
                if not_found and remaining:
                    self.network_queue[other] = (self.generation, self.revisions.get(other, 0), other)
                    self._store(other, replace(entry, remaining=remaining, attempted=attempted))
                else:
                    self._store(other, replace(entry, download=result if matches else None,
                                               pending=False, error=error, not_found=not_found,
                                               remaining=(), attempted=attempted))
                    if not_found and attempted == (self.preferences.provider_id,):
                        missing.append(other)
        if missing:
            self.missing.emit(missing)
        self._schedule()
