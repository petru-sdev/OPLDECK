"""Preparação, revisão e salvamento explícito de várias capas."""

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time

from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QMessageBox, QProgressBar, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from opldeck.covers.artwork import encode_image, prepare_cover
from opldeck.covers.cover_batch import CoverSaveJob, CoverSaveResult, save_covers
from opldeck.covers.cover_fallback import BackgroundFallback
from opldeck.covers.cover_preload import CoverPreloader
from opldeck.core.duration import duration_text


class SaveSignals(QObject):
    progress = Signal(object)
    finished = Signal(object)


class SaveCoversWorker(QRunnable):
    def __init__(self, jobs, preserve, volume):
        super().__init__()
        self.jobs, self.preserve, self.volume = jobs, preserve, volume
        self.cancel = Event()
        self.signals = SaveSignals()

    def run(self):
        outcomes = []
        def report(outcome):
            outcomes.append(outcome)
            self.signals.progress.emit(outcome)
        try:
            result = save_covers(self.jobs, preserve=self.preserve, volume=self.volume,
                                 cancel=self.cancel, progress=report)
        except Exception as exc:
            # Completed writes stay completed; untouched rows remain available.
            if len(outcomes) < len(self.jobs):
                outcomes.append((len(outcomes), 'Falhou', '\n'.join([str(exc), *getattr(exc, '__notes__', [])])))
            result = CoverSaveResult(tuple(outcomes), self.cancel.is_set())
        self.signals.finished.emit(result)


class CoverBatchDialog(QDialog):
    def __init__(self, keys, parent=None, *, settings=None, volume=None, preloader=None):
        super().__init__(parent)
        # One canonical target per library/serial, even when ISO and UL coexist.
        unique = {}
        for key in keys:
            unique.setdefault((key.root, key.game_id), key)
        self.keys = tuple(unique.values())
        self.rows = {key: row for row, key in enumerate(self.keys)}
        self.jobs = {}
        self.finished_rows = set()
        self.worker = None
        self.invalid = False
        self.preparing = True
        self.closed = False
        self.started = time.monotonic()
        self.volume = volume
        self.saved_paths = []
        self.spool = TemporaryDirectory(prefix='opl-deck-capas-')
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.preloader = preloader or CoverPreloader(self, settings=settings)
        self.preloader.reset(volume)
        self.fallback = BackgroundFallback(self.preloader, self)
        self.preloader.ready.connect(self.prepared)
        self.setWindowTitle('Salvar capas em lote')
        self.resize(980, 600)
        layout = QVBoxLayout(self)
        note = QLabel('Revise as prévias e marque as capas que deseja salvar. A biblioteca só será alterada ao clicar '
                      'em Salvar selecionadas. Originais JPEG e nomes legados são preservados.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.preserve = QCheckBox('Preservar capas existentes (incluindo JPEG e nomes legados)')
        self.preserve.setChecked(True)
        self.preserve.toggled.connect(self.update_selection)
        layout.addWidget(self.preserve)
        body = QHBoxLayout()
        self.table = QTableWidget(len(self.keys), 5)
        self.table.setHorizontalHeaderLabels(['Salvar', 'ID', 'Título', 'Estado', 'Biblioteca de destino'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, key in enumerate(self.keys):
            for col, text in enumerate(('', key.game_id, key.title, 'Preparando', str(key.root))):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 0:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    item.setCheckState(Qt.CheckState.Unchecked)
                self.table.setItem(row, col, item)
        for col, width in enumerate((55, 120, 165, 130, 240)):
            self.table.setColumnWidth(col, width)
        self.table.itemSelectionChanged.connect(self.show_preview)
        self.table.itemChanged.connect(self.selection_changed)
        body.addWidget(self.table, 1)
        self.preview = QLabel('Selecione um jogo para ver a capa.')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setWordWrap(True)
        self.preview.setFixedSize(220, 300)
        body.addWidget(self.preview)
        layout.addLayout(body, 1)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.progress = QProgressBar()
        self.progress.setRange(0, len(self.keys))
        layout.addWidget(self.progress)
        self.summary = QLabel('Preparando capas…')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QHBoxLayout()
        self.save = QPushButton('Salvar selecionadas')
        self.save.clicked.connect(self.start_save)
        self.save.setEnabled(False)
        buttons.addWidget(self.save)
        self.cancel = QPushButton('Cancelar preparação')
        self.cancel.clicked.connect(self.stop)
        buttons.addWidget(self.cancel)
        close = QPushButton('Fechar')
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.check_preparation)
        self.timer.start()
        QTimer.singleShot(0, lambda: self.preloader.preload(self.keys) if not self.closed else None)

    def prepared(self, key, entry):
        if self.closed or self.invalid or key not in self.rows or entry.pending:
            return
        row = self.rows[key]
        image = entry.download.image if entry.download else entry.local.image
        if image is not None and entry.local.state is not None:
            try:
                path = Path(self.spool.name) / f'{row}.png'
                path.write_bytes(encode_image(prepare_cover(image), '.png'))
                self.jobs[row] = CoverSaveJob(key, path, entry.local.state, entry.local.path is not None
                                              or entry.local.state.fingerprint is not None
                                              or entry.local.state.origin is not None)
                self.set_status(row, 'Pronta', entry.local.message.replace('Salvar alterações', 'Salvar selecionadas'))
            except (OSError, ValueError) as exc:
                self.set_status(row, 'Falhou', str(exc))
        else:
            self.set_status(row, 'Não encontrada' if entry.not_found else 'Indisponível',
                            entry.error or entry.local.message)
        self.update_selection(rows=(row,))
        if self.table.currentRow() == row:
            self.show_preview()

    def set_status(self, row, status, detail=''):
        item = self.table.item(row, 3)
        item.setText(status)
        item.setToolTip(detail or status)

    def check_preparation(self):
        if not self.preparing or self.closed:
            return
        count = sum(self.table.item(row, 3).text() != 'Preparando' for row in range(len(self.keys)))
        self.progress.setValue(count)
        self.summary.setText(f'Preparando capas · {count} de {len(self.keys)} · {duration_text(time.monotonic() - self.started)}')
        if not self.preloader.busy:
            self.preparing = False
            self.timer.stop()
            self.cancel.setEnabled(False)
            self.summary.setText(f'{len(self.jobs)} prévias disponíveis para revisão · {len(self.keys) - len(self.jobs)} indisponíveis.')
            self.update_buttons()

    def update_selection(self, *_, rows=None):
        # Recalculate eligibility once, instead of rescanning the batch for every
        # flag, checkbox and status change emitted by QTableWidget.
        preserve = self.preserve.isChecked()
        with QSignalBlocker(self.table):
            for row in self.jobs if rows is None else rows:
                job = self.jobs.get(row)
                if job is None:
                    continue
                allowed = not (preserve and job.existing) and row not in self.finished_rows
                item = self.table.item(row, 0)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                              (Qt.ItemFlag.ItemIsUserCheckable if allowed else Qt.ItemFlag.NoItemFlags))
                item.setCheckState(Qt.CheckState.Checked if allowed else Qt.CheckState.Unchecked)
                if row not in self.finished_rows:
                    self.set_status(row, 'Pronta' if allowed else 'Preservada', self.table.item(row, 3).toolTip())
        self.update_buttons()

    def selection_changed(self, item):
        if item.column() == 0:
            self.update_buttons()

    def selected_rows(self):
        return [row for row in self.jobs if row not in self.finished_rows
                and self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def update_buttons(self, *_):
        if hasattr(self, 'save'):
            self.save.setEnabled(not self.closed and not self.invalid and not self.preparing
                                 and self.worker is None and bool(self.selected_rows()))

    def show_preview(self):
        row = self.table.currentRow()
        job = self.jobs.get(row)
        self.preview.clear()
        if job:
            pixmap = QPixmap(str(job.preview))
            self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview.setText('Prévia indisponível.')
        if row >= 0:
            self.detail.setText(self.table.item(row, 3).toolTip())

    def start_save(self):
        self.update_buttons()
        if not self.save.isEnabled():
            return
        rows = self.selected_rows()
        replacements = [self.jobs[row].state.path for row in rows if self.jobs[row].state.fingerprint is not None]
        if replacements:
            box = QMessageBox(QMessageBox.Icon.Question, 'Substituir capas',
                              f'Substituir {len(replacements)} capas existentes pelas prévias selecionadas?',
                              QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            box.setDetailedText('\n'.join(map(str, replacements)))
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        # The confirmation runs a nested event loop: device removal or closing
        # the preview can invalidate its snapshots while the question is open.
        self.update_buttons()
        if not self.save.isEnabled() or rows != self.selected_rows():
            return
        self.saving_rows = rows
        self.worker = SaveCoversWorker(tuple(self.jobs[row] for row in rows), self.preserve.isChecked(), self.volume)
        self.worker.signals.progress.connect(self.saved_one)
        self.worker.signals.finished.connect(self.saved)
        self.preserve.setEnabled(False)
        self.table.setEnabled(False)
        self.cancel.setText('Cancelar salvamento')
        self.cancel.setEnabled(True)
        self.progress.setRange(0, len(rows))
        self.progress.setValue(0)
        self.started = time.monotonic()
        self.update_buttons()
        self.pool.start(self.worker)

    def saved_one(self, outcome):
        index, status, detail = outcome
        row = self.saving_rows[index]
        self.finished_rows.add(row)
        self.set_status(row, status, detail)
        if status == 'Salva':
            self.saved_paths.append(self.jobs[row].state.path)
        self.progress.setValue(index + 1)
        self.summary.setText(f'{index + 1} de {len(self.saving_rows)} processadas · {duration_text(time.monotonic() - self.started)}')

    def saved(self, result):
        # Apply results even when an unexpected worker exception interrupted reporting.
        for outcome in result.outcomes:
            if self.saving_rows[outcome[0]] not in self.finished_rows:
                self.saved_one(outcome)
        self.worker = None
        self.cancel.setEnabled(False)
        self.table.setEnabled(True)
        self.preserve.setEnabled(not self.invalid)
        count = sum(status == 'Salva' for _, status, _ in result.outcomes)
        failed = sum(status == 'Falhou' for _, status, _ in result.outcomes)
        pending = len(self.saving_rows) - len(result.outcomes)
        self.summary.setText(f'{"Cancelado" if result.cancelled else "Finalizado"} · {count} salvas · {failed} falhas · '
                             f'{pending} não processadas · {duration_text(time.monotonic() - self.started)}')
        self.update_selection(rows=self.saving_rows)

    def stop(self):
        if self.worker:
            self.worker.cancel.set()
            self.summary.setText('Cancelando… aguardando a gravação atual terminar.')
        elif self.preparing:
            self.preloader.close()
            self.preparing = False
            self.timer.stop()
            for row in range(len(self.keys)):
                if self.table.item(row, 3).text() == 'Preparando':
                    self.set_status(row, 'Cancelada')
            self.summary.setText('Preparação cancelada. As prévias já prontas podem ser salvas.')
            self.update_buttons()
        self.cancel.setEnabled(False)

    def invalidate(self, *_):
        self.invalid = True
        self.stop()
        self.preloader.close()
        self.summary.setText('Biblioteca ou pendrive alterado. Feche e prepare o lote novamente.')
        self.update_buttons()

    def done(self, result):
        if self.worker:
            self.stop()
            return
        self.closed = True
        self.update_buttons()
        self.timer.stop()
        self.preloader.close()
        self.spool.cleanup()
        super().done(result)

    def closeEvent(self, event):
        if self.worker:
            self.stop()
            event.ignore()
        else:
            super().closeEvent(event)
