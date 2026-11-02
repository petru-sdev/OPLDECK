"""Prévia Qt da reconstrução do índice, sem executar escritas em disco."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from opldeck.core.formats import FormatError
from opldeck.library.recovery import build_records, record_for_game


class RebuildDialog(QDialog):
    def __init__(self, scan, parent=None):
        super().__init__(parent)
        self.scan = scan
        self.setWindowTitle("Reconstruir ul.cfg · prévia")
        self.resize(1120, 620)
        layout = QVBoxLayout(self)
        description = QLabel(
            f"Biblioteca: {scan.directory}\n"
            "Informe o título original (ASCII, até 32 caracteres) e confirme CD/DVD. "
            "O CRC do título deve corresponder às partes; o CRC não permite descobrir o nome original.\n"
            + ("O ul.cfg existente será substituído após criar um backup com nome único. " if scan.index_data is not None
               else "Será criado um novo ul.cfg. ")
            + "As partes não serão renomeadas nem apagadas. Grupos desmarcados ficarão fora do novo índice.")
        description.setWordWrap(True)
        description.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(description)
        self.table = QTableWidget(len(scan.games), 8)
        self.table.setHorizontalHeaderLabels(["Recuperar", "ID", "CRC das partes", "Título original", "Mídia confirmada",
                                              "Partes", "Bytes", "Diagnóstico"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.fields = []
        for row, game in enumerate(scan.games):
            include = QTableWidgetItem()
            include.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            include.setCheckState(Qt.CheckState.Unchecked if game.errors else Qt.CheckState.Checked)
            if game.errors:
                include.setFlags(Qt.ItemFlag.NoItemFlags)
            self.table.setItem(row, 0, include)
            for column, value in [(1, game.game_id), (2, f"{game.crc:08X}"),
                                  (5, str(len(game.files))), (6, str(game.size)), (7, "")]:
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
            title = QLineEdit(game.title)
            title.setPlaceholderText("Título original desconhecido")
            title.setEnabled(not game.errors)
            media = QComboBox()
            media.addItems(["Confirmar…", "CD", "DVD"])
            if game.media:
                media.setCurrentText(game.media)
            media.setToolTip(f"Estimativa pelo tamanho: {game.estimated_media}. Confirme a mídia; não é uma identificação física do disco.")
            media.setEnabled(not game.errors)
            self.table.setCellWidget(row, 3, title)
            self.table.setCellWidget(row, 4, media)
            self.fields.append((include, title, media))
            title.textChanged.connect(self.validate)
            media.currentIndexChanged.connect(self.validate)
        for column, width in enumerate([70, 120, 110, 240, 140, 55, 100, 320]):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table)
        if scan.warnings:
            warnings = QPlainTextEdit("\n".join(scan.warnings))
            warnings.setReadOnly(True)
            warnings.setMaximumHeight(85)
            layout.addWidget(warnings)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        self.validation.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.validation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText("Reconstruir ul.cfg")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.itemChanged.connect(self.validate)
        self.validate()

    def choices(self):
        return [{"key": game.key, "include": include.checkState() == Qt.CheckState.Checked,
                 "title": title.text(), "media": media.currentText()}
                for game, (include, title, media) in zip(self.scan.games, self.fields)]

    def validate(self, *_):
        if not hasattr(self, "save_button"):
            return
        choices = self.choices()
        self.table.blockSignals(True)
        try:
            for row, (game, choice) in enumerate(zip(self.scan.games, choices)):
                if game.errors:
                    message = "; ".join(game.errors)
                elif not choice["include"]:
                    message = "Fora do novo índice; partes preservadas."
                else:
                    try:
                        record_for_game(game, choice["title"], choice["media"])
                        message = "ID interno e CRC conferidos; pronto para recuperar."
                    except FormatError as exc:
                        message = str(exc)
                self.table.item(row, 7).setText(message)
                self.table.item(row, 7).setToolTip(message)
        finally:
            self.table.blockSignals(False)
        try:
            records = build_records(self.scan, choices)
        except FormatError as exc:
            self.validation.setText(str(exc))
            self.save_button.setEnabled(False)
        else:
            count = len(records) // 64
            self.validation.setText(f"{count} jogo(s) no novo índice; {len(choices) - count} grupo(s) fora dele. "
                                   "A identificação não verifica todo o conteúdo por hash.")
            self.save_button.setEnabled(True)

    def accept(self):
        self.validate()
        if not self.save_button.isEnabled():
            return
        if self.scan.index_data is not None:
            count = sum(choice["include"] for choice in self.choices())
            answer = QMessageBox.question(
                self, "Substituir índice com backup",
                f"Substituir {self.scan.directory / 'ul.cfg'} por um índice com {count} jogo(s)?\n"
                "O arquivo anterior será preservado em um backup. Jogos desmarcados não constarão no novo índice.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        super().accept()
