"""Cadastro de fontes por endereço direto de capa."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit

from opldeck.covers.providers import custom_provider, validate_catalog


class ProviderDialog(QDialog):
    def __init__(self, parent=None, *, provider=None, others=()):
        super().__init__(parent)
        self.original = provider
        self.others = tuple(others)
        self.provider = None
        self.setWindowTitle('Editar fonte de capas' if provider else 'Nova fonte de capas')
        self.resize(640, 270)
        layout = QFormLayout(self)
        self.name = QLineEdit(provider.name if provider else '')
        self.name.setMaxLength(80)
        self.template = QLineEdit(provider.template if provider else '')
        self.template.setPlaceholderText('https://servidor/capas/{serial}.jpg')
        self.template.setMaxLength(2048)
        layout.addRow('Nome:', self.name)
        layout.addRow('Endereço da capa:', self.template)
        hint = QLabel('Informe o endereço direto de uma imagem JPEG/PNG. Use {id} para SLUS_207.18 '
                      'ou {serial} para SLUS-20718. Páginas de pesquisa e APIs com autenticação precisam de um adaptador próprio.')
        hint.setWordWrap(True)
        layout.addRow(hint)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        layout.addRow(self.feedback)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Concluir')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)
        self.name.textChanged.connect(self.validate)
        self.template.textChanged.connect(self.validate)
        self.validate()

    def validate(self):
        try:
            provider = custom_provider(self.name.text(), self.template.text(),
                                       key=self.original.key if self.original else None)
            validate_catalog((*self.others, provider))
        except ValueError as exc:
            self.provider = None
            self.feedback.setText(str(exc))
        else:
            self.provider = provider
            self.feedback.setText('Exemplo: ' + provider.cover_url('SLUS_207.18'))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.provider is not None)
        return self.provider is not None

    def accept(self):
        if self.validate():
            super().accept()
