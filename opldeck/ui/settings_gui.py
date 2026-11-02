"""Preferências de aparência e provedor de capas."""

from PySide6.QtCore import Qt, Signal, QStandardPaths, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QPushButton,
    QTabWidget, QVBoxLayout, QWidget, QHBoxLayout, QMessageBox, QFileDialog, QColorDialog, QSpinBox,
)

from opldeck.ui.themes import DENSITIES, THEMES, Personalization, theme_manager
from opldeck.branding import APP_NAME
from opldeck.covers.providers import PROVIDERS, ProviderPreferences
from opldeck.ui.provider_gui import ProviderDialog
from opldeck.library.import_folder import ImportPreferences


def theme_swatch(theme, native_palette):
    """Miniatura da superfície, campo e destaque sem aplicar a paleta."""
    swatch = QPixmap(84, 36)
    swatch.setDevicePixelRatio(2)
    painter = QPainter(swatch)
    for index, role in enumerate(('Window', 'Base', 'Highlight')):
        color = QColor(theme.colors[role]) if theme.colors else native_palette.color(getattr(QPalette.ColorRole, role))
        painter.fillRect(index * 14, 0, 14, 18, color)
    painter.end()
    return QIcon(swatch)


class SettingsDialog(QDialog):
    saved = Signal()

    def __init__(self, parent=None, *, initial_tab="appearance", manager=None):
        super().__init__(parent)
        self.manager = manager if manager is not None else theme_manager()
        self.original_theme = self.manager.current
        self.original_personalization = self.manager.personalization
        self.original_provider = ProviderPreferences.load(self.manager.settings)
        self.original_import = ImportPreferences.load(self.manager.settings)
        self.custom_providers = list(self.original_provider.custom)
        self.setWindowTitle(f"Configurações · {APP_NAME}")
        self.resize(700, 570)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        appearance = QWidget()
        form = QFormLayout(appearance)
        self.theme_combo = QComboBox()
        self.theme_combo.setIconSize(QSize(42, 18))
        for key, theme in THEMES.items():
            self.theme_combo.addItem(theme_swatch(theme, self.manager.native_palette), theme.name, key)
            self.theme_combo.setItemData(self.theme_combo.count() - 1, theme.description, Qt.ItemDataRole.ToolTipRole)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(self.manager.current))
        form.addRow("Tema", self.theme_combo)
        self.description = QLabel(THEMES[self.manager.current].description)
        self.description.setWordWrap(True)
        form.addRow(self.description)
        self.accent = self.original_personalization.accent or '#3977b5'
        self.accent_enabled = QCheckBox('Usar cor personalizada')
        self.accent_enabled.setChecked(bool(self.original_personalization.accent))
        self.accent_button = QPushButton()
        self.accent_button.clicked.connect(self.choose_accent)
        color_row = QHBoxLayout()
        color_row.addWidget(self.accent_enabled)
        color_row.addWidget(self.accent_button)
        form.addRow('Cor de destaque', color_row)
        self.update_accent_button()
        self.font_scale = QSpinBox()
        self.font_scale.setRange(80, 150)
        self.font_scale.setSingleStep(10)
        self.font_scale.setSuffix('%')
        self.font_scale.setValue(self.original_personalization.font_scale)
        form.addRow('Tamanho do texto', self.font_scale)
        self.density_combo = QComboBox()
        for key, (name, _) in DENSITIES.items():
            self.density_combo.addItem(name, key)
        self.density_combo.setCurrentIndex(self.density_combo.findData(self.original_personalization.density))
        form.addRow('Espaçamento das listas', self.density_combo)
        hint = QLabel("A escolha mostra uma prévia em todas as janelas. Aplicar salva a preferência; "
                      "Cancelar desfaz a prévia ainda não aplicada. As cores de diagnóstico dos jogos são mantidas.")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.reset_appearance = QPushButton('Restaurar aparência padrão')
        self.reset_appearance.clicked.connect(self.reset_personalization)
        form.addRow(self.reset_appearance)
        self.feedback = QLabel(self.manager.warning)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        self.tabs.addTab(appearance, "Aparência")
        providers = QWidget()
        page = QVBoxLayout(providers)
        self.provider_enabled = QCheckBox("Ativar busca online de capas")
        self.provider_enabled.setChecked(self.original_provider.enabled)
        page.addWidget(self.provider_enabled)
        self.provider_combo = QComboBox()
        for key, provider in self.original_provider.catalog.items():
            self.provider_combo.addItem(provider.name, key)
        self.provider_combo.setCurrentIndex(self.provider_combo.findData(self.original_provider.provider_id))
        page.addWidget(self.provider_combo)
        source_actions = QHBoxLayout()
        self.add_provider_button = QPushButton('Nova fonte…')
        self.edit_provider_button = QPushButton('Editar fonte…')
        self.remove_provider_button = QPushButton('Remover fonte')
        self.add_provider_button.clicked.connect(self.add_provider)
        self.edit_provider_button.clicked.connect(self.edit_provider)
        self.remove_provider_button.clicked.connect(self.remove_provider)
        for button in (self.add_provider_button, self.edit_provider_button, self.remove_provider_button):
            source_actions.addWidget(button)
        page.addLayout(source_actions)
        for text in (
            "As capas são procuradas primeiro somente na fonte selecionada, inclusive em segundo plano. Se a capa não for encontrada, uma única fonte alternativa é consultada automaticamente; havendo várias, o programa pergunta quais consultar.",
            "A imagem aparece como prévia. Use Salvar alterações no rodapé da ficha para gravar em ART; substituir uma capa existente exige confirmação.",
            "A escolha de imagens locais continua disponível mesmo com a busca online desativada.",
        ):
            label = QLabel(text)
            label.setWordWrap(True)
            page.addWidget(label)
        self.provider_source = QLabel()
        self.provider_source.setWordWrap(True)
        self.provider_source.setTextFormat(Qt.TextFormat.PlainText)
        page.addWidget(self.provider_source)
        page.addStretch()
        self.tabs.addTab(providers, "Provedores")
        imports = QWidget()
        page = QVBoxLayout(imports)
        self.import_folder = self.original_import.folder
        self.import_enabled = QCheckBox('Importar ZIP/7z automaticamente desta pasta')
        self.import_enabled.setChecked(self.original_import.enabled)
        page.addWidget(self.import_enabled)
        self.import_button = QPushButton('Pasta de import…')
        self.import_button.clicked.connect(self.choose_import_folder)
        page.addWidget(self.import_button)
        self.import_path = QLabel(self.import_folder or 'Nenhuma pasta cadastrada.')
        self.import_path.setTextFormat(Qt.TextFormat.PlainText)
        self.import_path.setWordWrap(True)
        page.addWidget(self.import_path)
        self.import_cleanup = QComboBox()
        for key, title in [('ask', 'Perguntar'), ('always', 'Sempre'), ('never', 'Nunca')]:
            self.import_cleanup.addItem(title, key)
        self.import_cleanup.setCurrentIndex(self.import_cleanup.findData(self.original_import.cleanup))
        page.addWidget(QLabel('Apagar os extraídos depois da transferência verificada:'))
        page.addWidget(self.import_cleanup)
        hint = QLabel('Enquanto o programa estiver aberto, os ZIP/7z desta pasta serão listados e os jogos '
                      'adicionados à lista. Use Iniciar → pendrive para revisar e transferir. '
                      'A extração usa uma pasta temporária no PC. Os arquivos compactados originais são sempre preservados. '
                      'Os temporários só podem ser apagados depois que todos os jogos forem transferidos e verificados. '
                      'Use uma subpasta dedicada em Downloads e mova para ela apenas downloads concluídos.')
        hint.setWordWrap(True)
        page.addWidget(hint)
        page.addStretch()
        self.tabs.addTab(imports, 'Importação')
        self.tabs.setCurrentIndex({'providers': 1, 'import': 2}.get(initial_tab, 0))
        layout.addWidget(self.tabs)
        # Mensagens de salvamento devem ser visíveis em ambas as abas.
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel |
                                  QDialogButtonBox.StandardButton.Apply)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Salvar e fechar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText("Aplicar")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.theme_combo.currentIndexChanged.connect(self.preview)
        self.accent_enabled.toggled.connect(self.preview)
        self.font_scale.valueChanged.connect(self.preview)
        self.density_combo.currentIndexChanged.connect(self.preview)
        self.provider_enabled.toggled.connect(self.provider_changed)
        self.provider_combo.currentIndexChanged.connect(self.provider_changed)
        self.import_enabled.toggled.connect(self.update_apply)
        self.import_cleanup.currentIndexChanged.connect(self.update_apply)
        self.provider_changed()

    def selected_personalization(self):
        return Personalization(self.accent if self.accent_enabled.isChecked() else '',
                               self.font_scale.value(), self.density_combo.currentData())

    def update_accent_button(self):
        self.accent_button.setEnabled(self.accent_enabled.isChecked())
        self.accent_button.setText(f'Escolher cor… {self.accent.upper()}')
        swatch = QPixmap(20, 20)
        swatch.fill(QColor(self.accent))
        self.accent_button.setIcon(QIcon(swatch))

    def choose_accent(self):
        color = QColorDialog.getColor(QColor(self.accent), self, 'Cor de destaque')
        if color.isValid():
            self.accent = color.name()
            self.preview()

    def reset_personalization(self):
        controls = (self.theme_combo, self.accent_enabled, self.font_scale, self.density_combo)
        for control in controls:
            control.blockSignals(True)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData('system'))
        self.accent_enabled.setChecked(False)
        self.accent = '#3977b5'
        self.font_scale.setValue(100)
        self.density_combo.setCurrentIndex(self.density_combo.findData('normal'))
        for control in controls:
            control.blockSignals(False)
        self.preview()

    def choose_import_folder(self):
        start = self.import_folder or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        folder = QFileDialog.getExistingDirectory(self, 'Pasta de import — escolha uma pasta dedicada', start)
        if folder:
            self.import_folder = folder
            self.import_path.setText(folder)
            self.import_enabled.setChecked(True)
            self.update_apply()

    def selected_import(self):
        return ImportPreferences(self.import_folder, self.import_enabled.isChecked(), self.import_cleanup.currentData())

    def selected_provider(self):
        return ProviderPreferences(self.provider_enabled.isChecked(), self.provider_combo.currentData(),
                                   tuple(self.custom_providers))

    def populate_providers(self, selected):
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for key, provider in {**PROVIDERS, **{p.key: p for p in self.custom_providers}}.items():
            self.provider_combo.addItem(provider.name, key)
        self.provider_combo.setCurrentIndex(max(0, self.provider_combo.findData(selected)))
        self.provider_combo.blockSignals(False)
        self.provider_changed()

    def add_provider(self):
        dialog = ProviderDialog(self, others=self.custom_providers)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.custom_providers.append(dialog.provider)
            self.populate_providers(dialog.provider.key)

    def edit_provider(self):
        key = self.provider_combo.currentData()
        current = next((p for p in self.custom_providers if p.key == key), None)
        if current is None:
            return
        dialog = ProviderDialog(self, provider=current, others=[p for p in self.custom_providers if p.key != key])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.custom_providers = [dialog.provider if p.key == key else p for p in self.custom_providers]
            self.populate_providers(key)

    def remove_provider(self):
        key = self.provider_combo.currentData()
        if key in PROVIDERS:
            return
        if QMessageBox.question(self, 'Remover fonte', f'Remover {self.provider_combo.currentText()} do cadastro?',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.custom_providers = [p for p in self.custom_providers if p.key != key]
        self.populate_providers('xlenore')

    def update_apply(self):
        self.apply_button.setEnabled(self.manager.current != self.original_theme or
                                     self.manager.personalization != self.original_personalization or
                                     self.selected_provider() != self.original_provider or
                                     self.selected_import() != self.original_import)

    def provider_changed(self, *_):
        custom = self.provider_combo.currentData() not in PROVIDERS
        self.edit_provider_button.setEnabled(custom)
        self.remove_provider_button.setEnabled(custom)
        self.add_provider_button.setEnabled(len(self.custom_providers) < 32)
        self.provider_source.setText("Fonte: " + self.selected_provider().catalog[self.provider_combo.currentData()].homepage)
        self.update_apply()

    def preview(self):
        key = self.theme_combo.currentData()
        try:
            self.manager.apply(key, self.selected_personalization())
        except ValueError as exc:
            self.feedback.setText(str(exc))
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(self.theme_combo.findData(self.manager.current))
            self.theme_combo.blockSignals(False)
            return
        self.description.setText(THEMES[key].description)
        self.update_accent_button()
        changed = key != self.original_theme or self.manager.personalization != self.original_personalization
        self.feedback.setText("Prévia não salva." if changed else "")
        self.update_apply()

    def save(self):
        if (self.manager.current == self.original_theme and self.selected_provider() == self.original_provider
                and self.manager.personalization == self.original_personalization
                and self.selected_import() == self.original_import
                and not self.manager.warning):
            return True
        try:
            self.manager.save({**self.selected_provider().values(), **self.selected_import().values()})
        except (OSError, ValueError) as exc:
            self.feedback.setText(str(exc))
            return False
        self.original_theme = self.manager.current
        self.original_personalization = self.manager.personalization
        self.original_provider = self.selected_provider()
        self.original_import = self.selected_import()
        self.apply_button.setEnabled(False)
        self.feedback.setText("Preferências salvas para as próximas aberturas.")
        self.saved.emit()
        return True

    def accept(self):
        if self.save():
            super().accept()

    def reject(self):
        if self.manager.current != self.original_theme or self.manager.personalization != self.original_personalization:
            self.manager.apply(self.original_theme, self.original_personalization)
        super().reject()
