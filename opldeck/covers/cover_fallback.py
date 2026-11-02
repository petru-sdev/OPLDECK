"""Escolha de fontes alternativas somente após a fonte principal retornar 404."""

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout


class AlternativeSourcesDialog(QDialog):
    def __init__(self, primary, alternatives, games, parent=None, *, background=False):
        super().__init__(parent)
        self.setWindowTitle('Consultar outras fontes?')
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.setText(f'{primary.name} não encontrou a capa de: ' + ', '.join(games) +
                             '\nEscolha as fontes que deseja consultar. A busca para na primeira capa encontrada.')
        layout.addWidget(self.message)
        self.sources = QListWidget()
        for index, provider in enumerate(alternatives):
            item = QListWidgetItem(provider.name)
            item.setData(Qt.ItemDataRole.UserRole, provider.key)
            item.setToolTip(provider.homepage)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if index == 0 else Qt.CheckState.Unchecked)
            self.sources.addItem(item)
        layout.addWidget(self.sources)
        if background:
            note = QLabel('A escolha vale para as capas que faltarem nesta leitura do pendrive. '
                          'Agora não mantém somente a fonte principal até a próxima leitura.')
            note.setWordWrap(True)
            layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.search = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.search.setText('Consultar fontes marcadas')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Agora não')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.sources.itemChanged.connect(lambda: self.search.setEnabled(bool(self.selected_sources())))
        layout.addWidget(buttons)

    def selected_sources(self):
        return tuple(self.sources.item(index).data(Qt.ItemDataRole.UserRole)
                     for index in range(self.sources.count())
                     if self.sources.item(index).checkState() == Qt.CheckState.Checked)

    def accept(self):
        if self.selected_sources():
            super().accept()


def choose_alternatives(parent, preferences, game_id):
    alternatives = preferences.alternatives
    if len(alternatives) == 1:
        return (alternatives[0].key,)
    if not alternatives:
        return ()
    dialog = AlternativeSourcesDialog(preferences.catalog[preferences.provider_id], alternatives,
                                      [game_id], parent)
    return dialog.selected_sources() if dialog.exec() == QDialog.DialogCode.Accepted else ()


class BackgroundFallback(QObject):
    def __init__(self, preloader, parent=None):
        super().__init__(parent)
        self.preloader = preloader
        self.policy = None
        self.keys = set()
        self.dialog = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._ask)
        preloader.missing.connect(self.missing)
        preloader.invalidated.connect(self.reset)

    def reset(self, *_):
        self.timer.stop()
        self.keys.clear()
        self.policy = None
        self.preloader.awaiting_choice = False
        dialog, self.dialog = self.dialog, None
        if dialog:
            dialog.reject()
            dialog.deleteLater()

    def missing(self, keys):
        preferences = self.preloader.preferences
        alternatives = preferences.alternatives
        if not alternatives:
            return
        if len(alternatives) == 1:
            self.preloader.try_alternatives(keys, (alternatives[0].key,))
        elif self.policy is not None:
            self.preloader.try_alternatives(keys, self.policy)
        else:
            self.keys.update(keys)
            self.preloader.awaiting_choice = True
            if self.dialog is None:
                self.timer.start(0)

    def _ask(self):
        if not self.keys or self.preloader.closed:
            return
        preferences = self.preloader.preferences
        dialog = AlternativeSourcesDialog(preferences.catalog[preferences.provider_id], preferences.alternatives,
                                          sorted({key.game_id for key in self.keys}), self.parent(), background=True)
        self.dialog = dialog
        dialog.finished.connect(lambda result: self._answered(dialog, result))
        dialog.show()

    def _answered(self, dialog, result):
        if dialog is not self.dialog:
            return
        self.policy = dialog.selected_sources() if result == QDialog.DialogCode.Accepted else ()
        self.dialog = None
        keys, self.keys = self.keys, set()
        self.preloader.awaiting_choice = False
        self.preloader.try_alternatives(keys, self.policy)
        self.preloader._schedule()
        dialog.deleteLater()
