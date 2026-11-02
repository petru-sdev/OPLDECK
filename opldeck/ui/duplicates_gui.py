"""Resultados de duplicidade na biblioteca carregada."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QLabel,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)


class DuplicatesDialog(QDialog):
    def __init__(self, groups, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Jogos duplicados por ID')
        self.resize(900, 480)
        layout = QVBoxLayout(self)
        count = sum(len(games) for games in groups.values())
        message = QLabel(f'{len(groups)} IDs repetidos · {count} ocorrências.\n'
                         'Comparação entre todas as ISOs e registros UL carregados, incluindo linhas ocultas pelos filtros. '
                         'Mesmo ID não garante conteúdo idêntico. Nenhum arquivo será apagado.')
        message.setWordWrap(True)
        layout.addWidget(message)
        self.table = QTableWidget(count, 5)
        self.table.setHorizontalHeaderLabels(['ID', 'Título', 'Formato', 'Registro UL', 'Caminho'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        row = 0
        for serial, games in groups.items():
            for game in games:
                for col, text in enumerate((serial, game.title, game.kind,
                                             str(game.record + 1) if game.kind == 'UL' else '—', str(game.path))):
                    item = QTableWidgetItem(text)
                    item.setToolTip(text)
                    self.table.setItem(row, col, item)
                row += 1
        for col, width in enumerate((125, 210, 80, 95, 360)):
            self.table.setColumnWidth(col, width)
        layout.addWidget(self.table)
        self.path_detail = QLabel('Selecione uma ocorrência para conferir e copiar o caminho completo.')
        self.path_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.path_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_detail.setWordWrap(True)
        self.table.itemSelectionChanged.connect(self.show_path)
        layout.addWidget(self.path_detail)
        if not groups:
            layout.addWidget(QLabel('Nenhum ID duplicado nas listas carregadas.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('Fechar')
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def show_path(self):
        row = self.table.currentRow()
        self.path_detail.setText(self.table.item(row, 4).text() if row >= 0 else '')
