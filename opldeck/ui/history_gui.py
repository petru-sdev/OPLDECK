"""Histórico de jogos transferidos."""

from dataclasses import asdict, dataclass
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from opldeck.branding import APP_NAME

HISTORY_SETTINGS_KEY = "history/transfers"
MAX_HISTORY_ITEMS = 1000


@dataclass(frozen=True)
class TransferHistoryEntry:
    timestamp: str
    game_id: str
    title: str
    operation: str
    source: str
    destination: str


def load_transfer_history(settings) -> list[TransferHistoryEntry]:
    if settings is None:
        return []
    raw = settings.value(HISTORY_SETTINGS_KEY, "")
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    entries = []
    for item in items:
        if not isinstance(item, dict):
            continue
        values = [item.get(k, "") for k in ("timestamp", "game_id", "title", "operation", "source", "destination")]
        if all(isinstance(v, str) for v in values):
            entries.append(TransferHistoryEntry(*values))
    return entries[-MAX_HISTORY_ITEMS:]


def save_transfer_history(settings, entries: list[TransferHistoryEntry]) -> None:
    if settings is None:
        return
    trimmed = entries[-MAX_HISTORY_ITEMS:]
    if trimmed:
        payload = json.dumps([asdict(entry) for entry in trimmed], ensure_ascii=False)
        settings.setValue(HISTORY_SETTINGS_KEY, payload)
    else:
        settings.remove(HISTORY_SETTINGS_KEY)
    settings.sync()


class TransferHistoryDialog(QDialog):
    def __init__(self, entries: list[TransferHistoryEntry], on_clear=None, parent=None):
        super().__init__(parent)
        self.entries = list(entries)
        self.on_clear = on_clear
        self.setWindowTitle(f"Histórico de transferências · {APP_NAME}")
        self.resize(920, 480)
        layout = QVBoxLayout(self)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Data/Hora", "ID", "Título", "Operação", "Origem", "Destino"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        for col, width in enumerate((155, 110, 200, 100, 165, 165)):
            self.table.setColumnWidth(col, width)
        layout.addWidget(self.table)
        self.detail_label = QLabel("Selecione um item para conferir os caminhos completos de origem e destino.")
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_label.setWordWrap(True)
        self.table.itemSelectionChanged.connect(self._update_detail)
        layout.addWidget(self.detail_label)
        footer = QHBoxLayout()
        self.clear_button = QPushButton("Limpar histórico")
        self.clear_button.clicked.connect(self.clear_history)
        footer.addWidget(self.clear_button)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fechar")
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self._populate()

    def _populate(self):
        sorting = self.table.isSortingEnabled()
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        try:
            self.table.setRowCount(len(self.entries))
            for row, entry in enumerate(self.entries):
                for col, text in enumerate((entry.timestamp, entry.game_id or "—", entry.title,
                                            entry.operation, entry.source, entry.destination)):
                    item = QTableWidgetItem(text)
                    item.setToolTip(text)
                    self.table.setItem(row, col, item)
        finally:
            self.table.setSortingEnabled(sorting)
            self.table.setUpdatesEnabled(True)
        count = len(self.entries)
        self.summary_label.setText(
            f"{count} jogo(s) transferido(s) registrado(s) no histórico."
            if count else "Nenhum jogo transferido registrado até o momento."
        )
        self.clear_button.setEnabled(bool(self.entries))
        self._update_detail()

    def _update_detail(self):
        row = self.table.currentRow()
        if 0 <= row < self.table.rowCount():
            source = self.table.item(row, 4).text()
            destination = self.table.item(row, 5).text()
            self.detail_label.setText(f"Origem: {source}\nDestino: {destination}")
        else:
            self.detail_label.setText("Selecione um item para conferir os caminhos completos de origem e destino.")

    def clear_history(self):
        self.entries.clear()
        if self.on_clear is not None:
            self.on_clear()
        self._populate()
