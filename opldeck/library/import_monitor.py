"""Observa a pasta cadastrada sem ler diretórios no thread da interface."""

import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from opldeck.library.import_folder import ImportPreferences, discover


class ImportMonitor(QObject):
    changed = Signal()

    def __init__(self, window, worker_type, settings):
        super().__init__(window)
        self.window = window
        self.worker_type = worker_type
        self.settings = settings
        self.preferences = ImportPreferences.load(settings)
        self.observed = {}
        self.attempted = set()
        self.records = {}
        self.loaded = set()
        self.worker = None
        self.closed = False
        self.generation = 0
        self.last_error = ''

        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def reset(self):
        self.generation += 1
        preferences = ImportPreferences.load(self.settings)
        if preferences.folder != self.preferences.folder:
            self.records.clear()
        self.preferences = preferences
        self.observed.clear()
        self.attempted.clear()
        self.last_error = ''
        self.changed.emit()

    def search(self):
        """Rever os candidatos agora, preservando a espera por arquivos estáveis."""
        if self.closed or self.worker or self.window.worker:
            return
        self.attempted.clear()
        self.poll()

    def close(self):
        self.closed = True
        self.generation += 1
        self.timer.stop()
        self.changed.emit()

    def poll(self):
        if self.closed or self.worker or self.window.worker or QApplication.activeModalWidget():
            return
        self.settings.sync()
        if ImportPreferences.load(self.settings) != self.preferences:
            self.reset()
        if not self.preferences.enabled:
            return
        generation = self.generation
        self.worker = self.worker_type(discover, self.preferences.folder)
        self.worker.signals.finished.connect(lambda result, error: self.scanned(generation, result, error))
        self.changed.emit()
        self.window.pool.start(self.worker)

    def report(self, message):
        if message and message != self.last_error:
            self.window.log('WARNING', message)
            self.window.statusBar().showMessage(message)
        self.last_error = message

    def scanned(self, generation, result, error):
        self.worker = None
        if self.closed or generation != self.generation:
            self.changed.emit()
            return
        if self.window.worker or QApplication.activeModalWidget():
            self.changed.emit()
            return
        if error:
            self.observed.clear()
            self.changed.emit()
            self.report('Pasta de import indisponível: ' + error)
            return
        identity, archives, records, errors = result
        previous, self.observed = self.observed, archives
        self.changed.emit()
        self.records.update({r['manifest']: r for r in records})
        self.report('\n'.join(errors))
        # Resume pending files after restart, without extracting the ZIP again.
        pending = []
        for record in records:
            if record['complete']:
                continue
            for name in record['games']:
                if name in self.loaded:
                    continue
                self.loaded.add(name)
                # Discovery already validates ownership; the ISO scan validates content.
                pending.append(name)
        if pending:
            self.window.load_isos(pending)
            return
        for name, expected in archives.items():
            key = (name, expected)
            if (key in self.attempted or previous.get(name) != expected
                    or time.time_ns() - expected[3] < 10_000_000_000):
                continue
            self.attempted.add(key)
            self.window.start_folder_archive(name, expected, self.preferences)
            return

    def register(self, record):
        self.records[record['manifest']] = record
        self.loaded.update(record['games'])
        self.observed.pop(record['archive'], None)
        self.changed.emit()

    def for_jobs(self, jobs):
        self.settings.sync()
        if ImportPreferences.load(self.settings) != self.preferences:
            self.reset()
        sources = {str(job.source) for job in jobs if job.kind == 'install'}
        return tuple(record for record in self.records.values()
                     if not record['complete'] and sources.intersection(record['files']))
