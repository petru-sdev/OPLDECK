"""Ficha por jogo com capas locais/online e atalhos para operações existentes."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QStyle, QTabWidget, QVBoxLayout, QWidget,
)

from opldeck.covers.artwork import decode_image, image_bytes, library_root, load_cover, prepare_cover, save_cover
from opldeck.covers.cover_network import CoverFetcher
from opldeck.covers.cover_preload import CoverKey
from opldeck.covers.cover_fallback import choose_alternatives
from opldeck.core.formats import validate_game_id
from opldeck.ui.preferences import application_settings
from opldeck.covers.providers import ProviderPreferences
from opldeck.ui.settings_gui import SettingsDialog


class GameDialog(QDialog):
    def __init__(self, values, kind, technical, *, editable=True, can_rename=False, can_delete=False,
                 can_convert=False, parent=None, settings=None, fetcher=None, library=None, library_guard=None,
                 preloader=None):
        super().__init__(parent)
        self.setWindowTitle("Ficha do jogo")
        self.resize(960, 670)
        self.game_id = values[1]
        self.root = library_root(values[6], kind) or (Path(library) if library else None)
        self.library_guard = library_guard
        self.game_title = values[2]
        self.game_filename = Path(values[6]).stem
        self.current_image = None
        self.current_cover_path = None
        self.state = None
        self.pending_image = None
        self.requested_action = None
        self.saved_paths = []
        self.settings = settings if settings is not None else application_settings()
        self.preloader = preloader
        self.preload_key = None
        self.accept_preloaded = True
        self.preloaded_pending = False
        self.local_received = False
        self.cover_loading = False
        self.closed = False
        self.search_preferences = None
        self.search_remaining = []
        self.search_attempted = []
        if self.preloader:
            self.preloader.ready.connect(self.preloaded_cover)
            self.preloader.invalidated.connect(self.preload_invalidated)
        self.fetcher = fetcher if fetcher is not None else CoverFetcher(
            self, manager=preloader.fetcher.manager if preloader else None)
        self.fetcher.progress.connect(self.download_progress)
        self.fetcher.finished.connect(self.download_finished)
        self.pending_source = ""
        try:
            validate_game_id(self.game_id)
            self.editable = editable
        except ValueError:
            self.editable = False
        layout = QVBoxLayout(self)
        title = QLabel(values[2])
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        font = title.font()
        font.setPointSize(16)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        header = QHBoxLayout()
        header.addWidget(QLabel(f"{self.game_id} · {kind} · {values[3]} · {values[4]}"))
        copy_id = QPushButton("Copiar ID")
        copy_id.setEnabled(self.game_id != '—')
        copy_id.clicked.connect(lambda: QApplication.clipboard().setText(self.game_id))
        header.addStretch()
        header.addWidget(copy_id)
        layout.addLayout(header)
        diagnosis = QLabel("Verificação: " + values[0])
        diagnosis.setTextFormat(Qt.TextFormat.PlainText)
        diagnosis.setWordWrap(True)
        layout.addWidget(diagnosis)
        body = QHBoxLayout()
        self.preview = QLabel("Sem capa")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(240, 340)
        cover_column = QVBoxLayout()
        cover_column.addWidget(self.preview, alignment=Qt.AlignmentFlag.AlignTop)
        self.cover_metadata = QLabel("Nenhuma capa carregada")
        self.cover_metadata.setWordWrap(True)
        self.cover_metadata.setMaximumWidth(240)
        self.cover_metadata.setTextFormat(Qt.TextFormat.PlainText)
        cover_column.addWidget(self.cover_metadata)
        self.zoom_button = QPushButton("Ampliar capa")
        self.zoom_button.clicked.connect(self.enlarge_cover)
        self.zoom_button.setEnabled(False)
        cover_column.addWidget(self.zoom_button)
        manage_cover = QPushButton("Gerenciar capa…")
        manage_cover.clicked.connect(lambda: tabs.setCurrentIndex(2))
        cover_column.addWidget(manage_cover)
        cover_column.addStretch()
        body.addLayout(cover_column)
        tabs = QTabWidget()
        info = QWidget()
        form = QFormLayout(info)
        for label, value in [("Título", values[2]), ("ID", values[1]), ("Mídia", values[3]),
                             ("Tamanho", values[4]), ("Formato", kind), ("Arquivo", values[6])]:
            field = QLineEdit(value)
            field.setReadOnly(True)
            form.addRow(label, field)
        note = QLabel("O ID e a mídia são lidos do jogo ou do índice. A capa pode ser escolhida localmente ou buscada pelo ID.")
        note.setWordWrap(True)
        form.addRow(note)
        tabs.addTab(info, "Informações")
        details = QPlainTextEdit(technical + "\n\nAs verificações disponíveis não comparam o conteúdo completo por hash.")
        details.setReadOnly(True)
        tabs.addTab(details, "Dados técnicos e diagnóstico")
        body.addWidget(tabs, 1)
        layout.addLayout(body)
        art_page = QWidget()
        art_layout = QVBoxLayout(art_page)
        root_row = QHBoxLayout()
        root_row.addWidget(QLabel("Biblioteca OPL:"))
        self.root_text = QLineEdit()
        self.root_text.setReadOnly(True)
        self.root_text.setPlaceholderText("Selecione a raiz da biblioteca para consultar ou salvar a capa")
        root_row.addWidget(self.root_text, 1)
        self.root_button = QPushButton("Escolher biblioteca…")
        self.root_button.clicked.connect(self.choose_root)
        self.root_button.setEnabled(self.editable)
        root_row.addWidget(self.root_button)
        art_layout.addLayout(root_row)
        self.cover_status = QLabel()
        self.cover_status.setTextFormat(Qt.TextFormat.PlainText)
        self.cover_status.setWordWrap(True)
        art_layout.addWidget(self.cover_status)
        art_row = QHBoxLayout()
        self.choose_button = QPushButton("Escolher capa…")
        self.choose_button.setEnabled(self.editable)
        self.choose_button.clicked.connect(self.choose_cover)
        self.save_button = QPushButton("Salvar alterações")
        self.save_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.save_button.clicked.connect(self.save)
        self.discard_button = QPushButton("Descartar prévia")
        self.discard_button.clicked.connect(self.discard)
        self.providers_button = QPushButton("Provedores…")
        self.providers_button.clicked.connect(self.configure_providers)
        for button in (self.choose_button, self.discard_button, self.providers_button):
            art_row.addWidget(button)
        art_row.addStretch()
        art_layout.addLayout(art_row)
        network_row = QHBoxLayout()
        self.search_button = QPushButton("Buscar capa online")
        self.search_button.clicked.connect(self.search_cover)
        self.download_bar = QProgressBar()
        self.download_bar.setRange(0, 1000)
        self.download_bar.setValue(0)
        self.download_bar.hide()
        self.cancel_search = QPushButton("Cancelar busca")
        self.cancel_search.clicked.connect(self.fetcher.cancel)
        self.cancel_search.hide()
        network_row.addWidget(self.search_button)
        network_row.addWidget(self.download_bar, 1)
        network_row.addWidget(self.cancel_search)
        art_layout.addLayout(network_row)
        self.network_status = QLabel()
        self.network_status.setTextFormat(Qt.TextFormat.PlainText)
        self.network_status.setWordWrap(True)
        art_layout.addWidget(self.network_status)
        art_layout.addStretch()
        tabs.addTab(art_page, "Capa e biblioteca")
        actions = QHBoxLayout()
        if kind == "UL":
            rename = QPushButton("Renomear jogo…")
            rename.setEnabled(can_rename and editable)
            rename.clicked.connect(lambda: self.finish_action("rename"))
            actions.addWidget(rename)
            delete = QPushButton('Excluir jogo…')
            delete.setEnabled(can_delete and editable)
            delete.clicked.connect(lambda: self.finish_action('delete'))
            actions.addWidget(delete)
        convert = QPushButton("Exportar para ISO…" if kind == "UL" else
                              "Converter para UL…" if kind == "ISO" else "Converter para ISO…")
        convert.setEnabled(can_convert and editable)
        convert.clicked.connect(lambda: self.finish_action("convert"))
        actions.addWidget(convert)
        actions.addWidget(self.save_button)
        folder = QPushButton("Abrir pasta do jogo")
        folder.clicked.connect(lambda: self.finish_action("folder"))
        actions.addWidget(folder)
        actions.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fechar")
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)
        self.reload_cover()
        self.refresh_provider()

    def display_image(self, image):
        self.current_image = image
        self.zoom_button.setEnabled(True)
        self.cover_metadata.setText(f"{image.width()} × {image.height()} pixels" +
                                    (" · " + self.current_cover_path.suffix.upper().lstrip('.') if self.current_cover_path else ' · prévia'))
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(
            self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def reload_cover(self, *, force=False):
        self.local_received = False
        self.cover_loading = False
        self.preload_key = None
        self.state = None
        self.current_image = None
        self.current_cover_path = None
        self.zoom_button.setEnabled(False)
        self.cover_metadata.setText("Nenhuma capa carregada")
        self.preview.clear()
        self.preview.setText("Sem capa")
        self.root_text.setText(str(self.root) if self.root else "")
        message = "Selecione a raiz da biblioteca OPL. A capa será gravada em ART/<ID>_COV.png."
        if self.root and self.game_id != "—":
            try:
                if self.library_guard:
                    self.library_guard(self.root, False)
                if self.preloader:
                    self.preload_key = CoverKey.make(self.root, self.game_id, self.game_title, self.game_filename)
                    self.cover_loading = True
                    self.cover_status.setText('Carregando capa…')
                    self.save_button.setEnabled(False)
                    self.discard_button.setEnabled(self.pending_image is not None and not self.fetcher.busy)
                    if self.pending_image is not None:
                        self.show_pending_cover()
                    else:
                        self.preview.setText('Carregando capa…')
                    prepared = self.preloader.request(self.preload_key, force=force)
                    if prepared is not None:
                        self.preloaded_cover(self.preload_key, prepared)
                    return
                loaded = load_cover(self.root, self.game_id, title=self.game_title, filename=self.game_filename)
                self.state = loaded.state
                self.current_cover_path = loaded.path
                if loaded.image is not None:
                    self.display_image(loaded.image)
                    self.prepare_local_cover(loaded)
                message = loaded.message
            except (OSError, ValueError) as exc:
                self.state = None
                message = str(exc)
        if not self.editable:
            message += "\nEdição indisponível durante uma operação ou sem um ID válido."
        self.cover_status.setText(message)
        if self.pending_image is not None:
            self.show_pending_cover()
        self.save_button.setEnabled(self.editable and not self.fetcher.busy and self.state is not None
                                    and self.pending_image is not None)
        self.discard_button.setEnabled(not self.fetcher.busy and self.pending_image is not None)

    def preloaded_cover(self, key, prepared):
        if self.closed or key != self.preload_key:
            return
        try:
            if self.library_guard:
                self.library_guard(self.root, False)
        except (OSError, ValueError) as exc:
            self.preload_invalidated()
            self.cover_status.setText(str(exc))
            return
        if not self.local_received:
            self.local_received = True
            self.cover_loading = False
            self.state = prepared.local.state
            self.current_cover_path = prepared.local.path
            self.prepare_local_cover(prepared.local)
            self.cover_status.setText(prepared.local.message)
            if not self.editable:
                self.cover_status.setText(self.cover_status.text() + '\nEdição indisponível durante uma operação ou sem um ID válido.')
            if self.pending_image is not None:
                self.show_pending_cover()
            elif prepared.local.image is not None:
                self.display_image(prepared.local.image)
            else:
                self.preview.setText('Sem capa')
        if self.accept_preloaded and self.pending_image is None:
            if prepared.download is not None:
                self.preloaded_pending = True
                self.pending_image = prepared.download.image
                self.pending_source = prepared.download.provider_name or prepared.download.provider_id
                self.show_pending_cover()
                self.network_status.setText('Capa pré-carregada. Confira e use Salvar alterações para gravar.')
            elif prepared.pending:
                self.network_status.setText('Preparando capa online em segundo plano…')
            elif prepared.error:
                self.network_status.setText(prepared.error)

    def prepare_local_cover(self, loaded):
        if self.accept_preloaded and self.pending_image is None and loaded.needs_save and loaded.state:
            self.pending_image = loaded.image
            self.preloaded_pending = True
            self.pending_source = 'Preparação da capa local para o OPL'

    def preload_invalidated(self, reason='library'):
        if self.closed:
            return
        self.search_remaining = []
        self.search_preferences = None
        self.fetcher.cancel()
        self.preload_key = None
        if reason == 'provider' and self.pending_image is not None and self.state is not None:
            # Changing network preferences must not accept a newer disk state
            # for a preview the user is already editing.
            return
        self.state = None
        self.cover_loading = False
        self.save_button.setEnabled(False)
        self.cover_status.setText('Biblioteca ou provedor alterado. Selecione a biblioteca novamente ou reabra a ficha.')

    def done(self, result):
        if self.preloader and not self.closed:
            self.preloader.ready.disconnect(self.preloaded_cover)
            self.preloader.invalidated.disconnect(self.preload_invalidated)
        self.closed = True
        super().done(result)

    def enlarge_cover(self):
        if self.current_image is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Capa · {self.game_title}")
        dialog.resize(760, 720)
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        picture = QLabel()
        picture.setPixmap(QPixmap.fromImage(self.current_image))
        scroll.setWidget(picture)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def configure_providers(self):
        dialog = SettingsDialog(self, initial_tab="providers")
        if self.preloader:
            dialog.saved.connect(self.preloader.sync_preferences)
        dialog.exec()
        if self.preloader:
            self.preloader.sync_preferences()
            if self.pending_image is None or self.state is None:
                self.reload_cover()
        self.refresh_provider()

    def refresh_provider(self):
        self.settings.sync()
        preferences = ProviderPreferences.load(self.settings)
        self.search_button.setEnabled(self.editable and preferences.enabled and not self.fetcher.busy)
        name = preferences.catalog[preferences.provider_id].name
        if self.pending_image is None and not self.cover_loading:
            self.network_status.setText(f"Provedor: {name} · busca por ID." if preferences.enabled else
                                        "Busca online desativada. Ative o provedor em Provedores…")

    def network_busy(self, busy):
        self.choose_button.setEnabled(self.editable and not busy)
        self.root_button.setEnabled(self.editable and not busy)
        self.providers_button.setEnabled(not busy)
        self.search_button.setEnabled(not busy and self.editable and ProviderPreferences.load(self.settings).enabled)
        self.save_button.setEnabled(not busy and self.editable and self.state is not None and self.pending_image is not None)
        self.discard_button.setEnabled(not busy and self.pending_image is not None)
        self.cancel_search.setVisible(busy)
        self.download_bar.setVisible(busy)

    def search_cover(self):
        if not self.editable or self.fetcher.busy:
            return
        preferences = ProviderPreferences.load(self.settings)
        if not preferences.enabled:
            self.refresh_provider()
            return
        if self.pending_image is not None and not self.preloaded_pending and QMessageBox.question(
            self, "Prévia não salva", "Buscar outra capa? A prévia atual só será trocada se a busca terminar com sucesso.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.accept_preloaded = False
        self.fetcher.providers = preferences.catalog
        self.search_preferences = preferences
        self.search_remaining = [preferences.provider_id]
        self.search_attempted = []
        self.start_search_source()

    def start_search_source(self):
        provider_id = self.search_remaining.pop(0)
        self.search_attempted.append(provider_id)
        self.network_busy(True)
        self.network_status.setText(f"Buscando capa de {self.game_id} em {self.search_preferences.catalog[provider_id].name}…")
        self.download_bar.setRange(0, 0)
        try:
            self.fetcher.start(provider_id, self.game_id)
        except ValueError as exc:
            self.network_busy(False)
            self.network_status.setText(str(exc))

    def download_progress(self, received, total):
        if total:
            self.download_bar.setRange(0, 1000)
            self.download_bar.setValue(min(1000, received * 1000 // total))
        else:
            self.download_bar.setRange(0, 0)

    def download_finished(self, result, error):
        self.network_busy(False)
        if error:
            self.network_status.setText(error)
            if (self.fetcher.last_http_status == 404 and self.search_preferences is not None
                    and not self.closed):
                current = ProviderPreferences.load(self.settings)
                if current != self.search_preferences or not current.enabled:
                    self.search_remaining = []
                    return
                if self.search_attempted == [current.provider_id]:
                    selected = choose_alternatives(self, current, self.game_id)
                    # The modal chooser processes device/settings events too.
                    # Never resume a search invalidated while it was open.
                    if (self.closed or self.search_preferences != current
                            or ProviderPreferences.load(self.settings) != current):
                        self.search_remaining = []
                        return
                    self.search_remaining = list(selected)
                if self.search_remaining:
                    self.start_search_source()
            else:
                self.search_remaining = []
            return
        self.pending_image = result.image
        self.preloaded_pending = False
        self.pending_source = result.provider_name or result.provider_id
        self.show_pending_cover()
        self.network_status.setText("Capa encontrada. Confira a prévia e use Salvar alterações, no rodapé da ficha, para gravar na biblioteca.")

    def show_pending_cover(self):
        # Keep the original save guard: choosing a preview must not accept an
        # external change to the cover that happened since opening the ficha.
        self.current_cover_path = None
        self.pending_image = prepare_cover(self.pending_image)
        self.display_image(self.pending_image)
        self.cover_metadata.setText(f'{self.pending_image.width()} × {self.pending_image.height()} pixels · PNG para o OPL')
        message = self.cover_status.text().split("\nAlteração pendente", 1)[0]
        message += "\nAlteração pendente — use Salvar alterações, no rodapé da ficha, para gravar."
        if self.pending_source:
            message += " Fonte: " + self.pending_source
        if self.state and self.state.origin:
            message += f'\nO original {self.state.origin.name} será preservado; saída: {self.state.path.name}.'
        self.cover_status.setText(message)
        self.save_button.setEnabled(self.editable and not self.fetcher.busy and self.state is not None)
        self.discard_button.setEnabled(not self.fetcher.busy)

    def choose_root(self):
        directory = QFileDialog.getExistingDirectory(self, "Raiz da biblioteca OPL (contém ART, CD/DVD ou ul.cfg)",
                                                     str(self.root or Path.home()))
        if directory:
            self.root = Path(directory)
            self.accept_preloaded = self.pending_image is None
            self.reload_cover(force=True)

    def choose_cover(self):
        if self.fetcher.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Escolher capa local", "", "Imagens (*.jpg *.jpeg *.png)")
        if not path:
            return
        try:
            image = decode_image(image_bytes(path))
            self.accept_preloaded = False
            self.preloaded_pending = False
            self.pending_image = image
            self.pending_source = "Arquivo local"
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Capa inválida", str(exc))
            return
        self.show_pending_cover()

    def save(self):
        if not self.editable or self.fetcher.busy or self.state is None or self.pending_image is None:
            return
        if self.state.fingerprint is not None and QMessageBox.question(
            self, "Substituir capa", f"Substituir a capa existente?\n{self.state.path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            if self.library_guard:
                self.library_guard(self.root, True)
            path = save_cover(self.root, self.game_id, self.pending_image, self.state)
        except (OSError, ValueError) as exc:
            message = "\n".join([str(exc), *getattr(exc, "__notes__", [])])
            QMessageBox.warning(self, "Falha ao salvar capa", message)
            return
        self.saved_paths.append(path)
        self.accept_preloaded = False
        self.pending_image = None
        self.pending_source = ""
        self.reload_cover(force=True)
        self.cover_status.setText(f"Capa salva em {path}")

    def discard(self):
        if self.fetcher.busy:
            return
        self.pending_image = None
        self.accept_preloaded = False
        self.pending_source = ""
        self.reload_cover(force=True)

    def can_close(self):
        return self.pending_image is None or self.preloaded_pending or QMessageBox.question(
            self, "Prévia não salva", "Descartar a capa selecionada que ainda não foi salva?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Discard

    def reject(self):
        if self.can_close():
            self.fetcher.cancel()
            super().reject()

    def finish_action(self, action):
        if self.can_close():
            self.fetcher.cancel()
            self.requested_action = action
            self.accept()
