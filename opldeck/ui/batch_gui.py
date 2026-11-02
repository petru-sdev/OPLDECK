"""Revisão da seleção e do destino antes de iniciar uma transferência em lote."""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from opldeck.core.batch import OPERATIONS, TransferJob, validate_jobs
from opldeck.core.formats import Media
from opldeck.library.devices import require_volume
from opldeck.core.iso import infer_media
from opldeck.core.formats import CD_SIZE_THRESHOLD


class BatchDialog(QDialog):
    def __init__(self, values, kind, cfg_path=None, parent=None, *, volume=None):
        super().__init__(parent)
        self.kind, self.values, self.cfg_path = kind, list(values), cfg_path
        self.volume = volume
        self.setWindowTitle(f"Transferência em lote · {OPERATIONS[kind]}")
        self.resize(880, 510)
        layout = QVBoxLayout(self)
        description = QLabel(
            f"{len(values)} jogos · transferência sequencial. "
            + ("Revise os títulos e confirme CD/DVD de cada jogo." if kind == "install" else
               "Cada jogo será salvo como <ID>.iso na pasta de destino.")
            + "\nSe houver falha ou cancelamento, a fila para e os jogos concluídos são mantidos."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.auto_media = QCheckBox('Escolher CD/DVD automaticamente')
        self.auto_media.setVisible(kind == 'install')
        layout.addWidget(self.auto_media)
        self.media_hint = QLabel(
            'Automático: usa a pasta CD/DVD; fora dela, estima pelo tamanho. '
            f'Abaixo de {CD_SIZE_THRESHOLD:,} bytes: CD; a partir desse valor: DVD. '
            'É uma estimativa experimental; desmarque para corrigir manualmente.')
        self.media_hint.setWordWrap(True)
        self.media_hint.setVisible(kind == 'install')
        layout.addWidget(self.media_hint)
        self.table = QTableWidget(len(values), 5)
        self.table.setHorizontalHeaderLabels(["ID", "Título", "Mídia", "Tamanho de saída", "Origem"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.titles, self.media = [], []
        for row, value in enumerate(values):
            for column, text in [(0, value[1]), (3, f"{int(value[8] if kind == 'bin' else value[7]):,} bytes"),
                                 (4, value[6])]:
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, column, item)
            title = QLineEdit(value[2])
            title.setReadOnly(kind != "install")
            self.table.setCellWidget(row, 1, title)
            self.titles.append(title)
            media = QComboBox()
            media.addItems(["Confirmar…", "CD", "DVD"])
            if value[3] in {"CD", "DVD"}:
                media.setCurrentText(value[3])
            media.setEnabled(kind == "install")
            self.table.setCellWidget(row, 2, media)
            self.media.append(media)
        for col, width in enumerate([120, 240, 110, 150, 230]):
            self.table.setColumnWidth(col, width)
        layout.addWidget(self.table)
        order = QHBoxLayout()
        order.addWidget(QLabel('Ordem de transferência:'))
        for label, offset in [('Mover acima', -1), ('Mover abaixo', 1)]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, delta=offset: self.move_selected(delta))
            order.addWidget(button)
        order.addStretch()
        layout.addLayout(order)
        row = QHBoxLayout()
        row.addWidget(QLabel("Destino:"))
        self.destination = QLineEdit()
        self.destination.setReadOnly(True)
        self.destination.setPlaceholderText("Raiz da biblioteca UL" if kind == "install" else "Pasta para as novas ISOs")
        row.addWidget(self.destination, 1)
        choose = QPushButton("Escolher pasta…")
        choose.clicked.connect(self.choose_destination)
        choose.setVisible(volume is None)
        row.addWidget(choose)
        layout.addLayout(row)
        self.validation = QLabel()
        self.validation.setTextFormat(Qt.TextFormat.PlainText)
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.start_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.start_button.setText("Iniciar transferência")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for title, media in zip(self.titles, self.media):
            title.textChanged.connect(self.validate)
            media.currentIndexChanged.connect(self.validate)
        self.destination.textChanged.connect(self.validate)
        self.manual_media = [media.currentText() for media in self.media]
        self.auto_media.toggled.connect(self.update_media_mode)
        if volume:
            self.destination.setText(str(volume.root))
        self.device_timer = QTimer(self)
        self.device_timer.setInterval(500)
        self.device_timer.timeout.connect(self.validate)
        if volume:
            self.device_timer.start()
        self.validate()

    def move_selected(self, offset):
        row = self.table.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < len(self.values):
            return
        # Swap row data, preserving editable titles and both media modes.
        for widgets, getter, setter in ((self.titles, 'text', 'setText'),
                                         (self.media, 'currentText', 'setCurrentText')):
            first, second = widgets[row], widgets[target]
            left, right = getattr(first, getter)(), getattr(second, getter)()
            with QSignalBlocker(first), QSignalBlocker(second):
                getattr(first, setter)(right)
                getattr(second, setter)(left)
        self.values[row], self.values[target] = self.values[target], self.values[row]
        self.manual_media[row], self.manual_media[target] = self.manual_media[target], self.manual_media[row]
        for col in (0, 3, 4):
            first, second = self.table.takeItem(row, col), self.table.takeItem(target, col)
            self.table.setItem(row, col, second)
            self.table.setItem(target, col, first)
        self.table.selectRow(target)
        self.validate()

    def update_media_mode(self, automatic):
        if automatic:
            self.manual_media = [media.currentText() for media in self.media]
        for value, combo, manual in zip(self.values, self.media, self.manual_media):
            combo.blockSignals(True)
            if automatic:
                media, estimated = infer_media(value[6], int(value[7]))
                combo.setCurrentText(media.name)
                combo.setToolTip('Estimativa por tamanho; confirme se necessário.' if estimated else 'Mídia pela pasta CD/DVD.')
            else:
                combo.setCurrentText(manual)
                combo.setToolTip('Escolha manual de mídia.')
            combo.setEnabled(not automatic and self.kind == 'install')
            combo.blockSignals(False)
        self.validate()

    def choose_destination(self):
        path = QFileDialog.getExistingDirectory(self, "Destino dos jogos")
        if path:
            self.destination.setText(path)

    def jobs(self):
        if self.volume:
            require_volume(self.volume, write=True)
            if self.destination.text() != str(self.volume.root):
                raise ValueError('O destino difere do pendrive selecionado.')
        if not self.destination.text():
            raise ValueError("Escolha a pasta de destino.")
        root = Path(self.destination.text())
        if not root.is_dir():
            raise ValueError("Escolha uma pasta de destino existente.")
        jobs = []
        for value, title, media in zip(self.values, self.titles, self.media):
            if self.kind == "install" and media.currentText() not in {"CD", "DVD"}:
                raise ValueError(f"Confirme CD/DVD para {title.text()}.")
            jobs.append(TransferJob(
                self.kind, Path(self.cfg_path) if self.kind == "export" else Path(value[6]),
                root if self.kind == "install" else root / f"{value[1]}.iso",
                value[1], title.text(), int(value[8] if self.kind == "bin" else value[7]),
                Media[media.currentText()] if self.kind == "install" else None,
            ))
        validate_jobs(jobs)
        return tuple(jobs)

    def validate(self, *_):
        try:
            jobs = self.jobs()
        except (OSError, ValueError) as exc:
            self.validation.setText(str(exc))
            self.start_button.setEnabled(False)
            return False
        self.validation.setText(f"{len(jobs)} jogos · {sum(job.size for job in jobs):,} bytes de saída. "
                                "O espaço e possíveis conflitos serão verificados antes da transferência.")
        self.start_button.setEnabled(True)
        return True

    def accept(self):
        if self.validate():
            super().accept()
