"""Fila da sessão: preserva resultados e repete somente jogos não concluídos."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from opldeck.core.batch import OPERATIONS


class TransferQueue(QWidget):
    retry_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs = ()
        self.indices = []
        self.states = []
        self.volume = None
        self.running = False
        self.single_retry = None
        layout = QVBoxLayout(self)
        self.summary = QLabel('Nenhuma transferência nesta sessão.')
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        note = QLabel('A ordem pode ser ajustada na revisão do lote. Tentar novamente reinicia os jogos '
                      'não concluídos e verifica origens, espaço e conflitos. A fila é mantida até o próximo lote ou fechamento.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['Estado', 'Progresso', 'ID', 'Título', 'Operação', 'Origem', 'Destino'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for column, width in enumerate((150, 90, 120, 200, 110, 250, 250)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        self.retry = QPushButton('Tentar novamente os não concluídos')
        self.retry.clicked.connect(self.retry_requested)
        self.retry.setEnabled(False)
        buttons.addWidget(self.retry)
        self.cancel = QPushButton('Cancelar fila')
        self.cancel.clicked.connect(self.cancel_requested)
        self.cancel.setEnabled(False)
        buttons.addWidget(self.cancel)
        buttons.addStretch()
        layout.addLayout(buttons)

    @property
    def pending_jobs(self):
        return tuple(job for job, state in zip(self.jobs, self.states) if state != 'Concluído')

    def begin(self, jobs, volume=None, *, retry=False):
        if retry:
            if tuple(jobs) != self.pending_jobs:
                raise ValueError('A seleção para repetir difere da fila.')
            self.indices = [i for i, state in enumerate(self.states) if state != 'Concluído']
        else:
            self.single_retry = None
            self.jobs = tuple(jobs)
            self.volume = volume
            self.states = ['Pendente'] * len(jobs)
            self.indices = list(range(len(jobs)))
            self.table.setRowCount(len(jobs))
            for row, job in enumerate(jobs):
                for col, text in enumerate(('Pendente', '0%', job.game_id, job.title,
                                             OPERATIONS[job.kind], str(job.source), str(job.destination))):
                    item = QTableWidgetItem(text)
                    item.setToolTip(text)
                    self.table.setItem(row, col, item)
        for row in self.indices:
            self.set_state(row, 'Pendente', 0)
        self.running = True
        self.set_busy(True)
        self.summarize()

    def set_state(self, row, state, percent=None, detail=''):
        self.states[row] = state
        self.table.item(row, 0).setText(state)
        self.table.item(row, 0).setToolTip(detail or state)
        if percent is not None:
            self.table.item(row, 1).setText(f'{percent}%')

    def progress(self, event):
        if not self.running or not 0 < event.index <= len(self.indices):
            return
        row = self.indices[event.index - 1]
        state = {'preparing': 'Preparando', 'transfer': 'Transferindo',
                 'sync': 'Sincronizando', 'complete': 'Concluído'}.get(event.phase, 'Transferindo')
        percent = 100 if event.phase == 'complete' else min(99, event.completed * 100 // max(1, event.total))
        self.set_state(row, state, percent)
        self.summarize()

    def finish(self, result):
        if not self.running:
            return
        count = len(result.completed)
        for offset, row in enumerate(self.indices):
            if offset < count:
                self.set_state(row, 'Concluído', 100)
            elif offset < result.attempted:
                self.set_state(row, 'Cancelado' if result.cancelled else 'Falhou', detail=result.error)
            else:
                self.set_state(row, 'Pendente', 0, result.error)
        self.running = False
        self.set_busy(False)
        self.summarize(result.error)

    def set_busy(self, busy):
        self.retry.setEnabled(not busy and not self.running and bool(self.pending_jobs))
        self.cancel.setEnabled(self.running and busy)

    def summarize(self, error=''):
        done = self.states.count('Concluído')
        self.summary.setText(f'{done} de {len(self.jobs)} jogos concluídos · {len(self.jobs) - done} não concluídos'
                             + ('\n' + error if error else ''))
