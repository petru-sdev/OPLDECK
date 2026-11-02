"""Listagem e senha em memória antes da importação de arquivos compactados."""

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from opldeck.library.archives import PasswordRequired, WrongPassword, list_archive


@dataclass(frozen=True)
class ProbeRequest:
    path: Path
    password: str | None = field(default=None, repr=False)


def probe_archive(request):
    try:
        return list_archive(request.path, password=request.password), ''
    except PasswordRequired:
        return None, 'password'
    except WrongPassword:
        return None, 'wrong_password'


class ArchivePreview(QDialog):
    def __init__(self, plans, parent=None):
        super().__init__(parent)
        self.plans = plans
        self.setWindowTitle('Conteúdo dos arquivos compactados')
        self.resize(850, 500)
        layout = QVBoxLayout(self)
        hint = QLabel('Selecione os jogos para importar. ISO e pares BIN/CUE estão marcados; os demais arquivos serão ignorados. '
                      'Os originais serão preservados. BIN/CUE será convertido para ISO no PC antes da transferência.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Importar / arquivo', 'Compactado', 'Tamanho extraído'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.rows = []
        for index, plan in enumerate(plans):
            for member in plan.members:
                if member.directory:
                    continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                item = QTableWidgetItem(member.name)
                if member.name in plan.selected:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked)
                else:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, 0, item)
                self.table.setItem(row, 1, QTableWidgetItem(plan.source.name))
                self.table.setItem(row, 2, QTableWidgetItem(f'{member.size / (1 << 20):.2f} MiB'))
                self.rows.append((index, member.name, item))
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.start = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.start.setText('Extrair jogos selecionados')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.table.itemChanged.connect(self.selection_changed)
        layout.addWidget(buttons)

    def selection_changed(self, changed):
        self.table.blockSignals(True)
        try:
            for owner, name, item in self.rows:
                if item is changed:
                    related = {name}
                    for cue, binary in self.plans[owner].pairs:
                        if name in (cue, binary):
                            related.update((cue, binary))
                    for other_owner, other_name, other in self.rows:
                        if owner == other_owner and other_name in related:
                            other.setCheckState(changed.checkState())
                    break
        finally:
            self.table.blockSignals(False)
        self.start.setEnabled(any(item.checkState() == Qt.CheckState.Checked for _, _, item in self.rows))

    def selected_plans(self):
        result = []
        for index, plan in enumerate(self.plans):
            selected = [name for owner, name, item in self.rows if owner == index and item.checkState() == Qt.CheckState.Checked]
            if selected:
                result.append(plan.select(selected))
        return result

    def accept(self):
        if self.selected_plans():
            super().accept()


class ArchiveImportController(QObject):
    def __init__(self, window, worker_type):
        super().__init__(window)
        self.window, self.worker_type = window, worker_type
        self.active = False
        self.queue, self.plans, self.errors = [], [], []
        self.request = None
        self.automatic = None

    def start(self, paths, *, automatic=None):
        if self.active or self.window.worker is not None:
            return
        self.active = True
        self.queue = list(dict.fromkeys(map(Path, paths)))
        self.plans, self.errors = [], []
        self.automatic = automatic
        self.window._busy(True)
        self.next_file()

    def next_file(self):
        if not self.queue:
            self.finish()
            return
        self.request = ProbeRequest(self.queue.pop(0))
        self.read()

    def read(self):
        self.window.activity.begin('Listando arquivo compactado…', self.request.path.name, scan=True)
        worker = self.worker_type(probe_archive, self.request)
        self.window.worker = worker
        worker.signals.finished.connect(self.listed)
        self.window.pool.start(worker)

    def listed(self, result, error):
        self.window.worker = None
        if not error and result[1] in ('password', 'wrong_password'):
            text = 'Senha incorreta. Informe novamente:' if result[1] == 'wrong_password' else 'Senha do arquivo protegido:'
            password, accepted = QInputDialog.getText(self.window, self.request.path.name, text, QLineEdit.EchoMode.Password)
            if accepted:
                self.request = ProbeRequest(self.request.path, password)
                self.read()
                return
            self.errors.append(f'{self.request.path.name}: senha não fornecida; arquivo ignorado.')
        elif error:
            self.errors.append(f'{self.request.path.name}: {error}')
        else:
            self.plans.append(result[0])
        self.request = None
        self.next_file()

    def finish(self):
        self.window.activity.finish_scan('Listagem concluída.')
        plans, self.plans = self.plans, []
        self.active = False
        self.window._busy(False)
        if self.errors:
            QMessageBox.warning(self.window, 'Arquivos não importados', '\n'.join(self.errors))
        if not plans:
            return
        if self.automatic is None:
            preview = ArchivePreview(plans, self.window)
            if preview.exec() != QDialog.DialogCode.Accepted:
                return
            plans = preview.selected_plans()
        self.window.start_managed_archives(plans, automatic=self.automatic)
