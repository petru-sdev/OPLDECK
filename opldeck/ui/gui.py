"""Interface Qt de inspeção, conversão e diagnóstico técnico."""

from contextlib import contextmanager
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import shutil
from threading import Event
import time
import unicodedata

from PySide6.QtCore import QEvent, QObject, QRect, QRunnable, QSignalBlocker, QThreadPool, QTimer, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPalette, QPen, QPixmap, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QInputDialog, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from opldeck import __version__
from opldeck.branding import APP_NAME
from opldeck.ui.activity_gui import ActivityPanel
from opldeck.ui.about_gui import AboutPage
from opldeck.library.archives import ArchiveResult, extract_archives, fingerprint
from opldeck.core.batch import OPERATIONS, BatchProgress, BatchResult, TransferJob, run_batch
from opldeck.ui.batch_gui import BatchDialog
from opldeck.core.binimage import RAW_SECTOR, convert_bin, inspect_bin
from opldeck.core.conversion import BUFFER_SIZE, TRANSACTION_DIR, CancelledError, Progress, export_ul, install_iso
from opldeck.covers.artwork import library_root
from opldeck.covers.cover_preload import CoverKey, CoverPreloader
from opldeck.covers.cover_fallback import BackgroundFallback
from opldeck.core.crc import usba_crc32
from opldeck.library.devices import DeviceMonitor, library_files, require_volume, volume_available
from opldeck.library.deletion import DeleteProgress, DeleteResult, delete_selection, prepare_delete
from opldeck.core.formats import CD_SIZE_THRESHOLD, Media, PART_SIZE
from opldeck.library.fileio import regular_reader
from opldeck.ui.game_gui import GameDialog
from opldeck.core.iso import inspect_iso
from opldeck.library.import_folder import (FolderImportResult, FolderImportBatch, FolderTransferResult, ImportPreferences,
                            finish_imports, prepare_archive, prepare_imports, workspace_root)
from opldeck.library.import_monitor import ImportMonitor
from opldeck.ui.archive_gui import ArchiveImportController
from opldeck.library.management import delete_ul, recover_ul, rename_ul
from opldeck.library.recovery import rebuild_ul, scan_ul
from opldeck.ui.recovery_gui import RebuildDialog
from opldeck.ui.settings_gui import SettingsDialog
from opldeck.ui.themes import STATUS_COLORS, status_color, theme_manager
from opldeck.ui.progress_gui import OperationProgressBar
from opldeck.core.transfer_rate import TransferRate
from opldeck.core.duration import duration_text
from opldeck.ui.queue_gui import TransferQueue
from opldeck.library.duplicates import GameOccurrence, duplicate_games
from opldeck.ui.duplicates_gui import DuplicatesDialog
from opldeck.ui.history_gui import TransferHistoryDialog, TransferHistoryEntry, load_transfer_history, save_transfer_history
from opldeck.ui.cover_batch_gui import CoverBatchDialog
from opldeck.core.ul import ULEntry, read_cfg

HEADERS = ["Status", "ID", "Título", "Mídia", "Tamanho", "Partes UL", "Caminho",
           "Bytes", "CRC USBExtreme", "Identificação / índice", "Origem da mídia"]
BIN_HEADERS = ["Status", "ID", "Título", "Mídia", "Tamanho raw", "Setores", "Caminho",
               "Bytes raw", "Bytes ISO", "Formato de setor", "BIN de origem"]
STATUS_ROLE = Qt.ItemDataRole.UserRole + 1
SEARCH_ROLE = Qt.ItemDataRole.UserRole + 2
STATUS_LABELS = {"ok": "Sem falhas na verificação", "warning": "Atenção / verificação parcial", "error": "Falha detectada"}


def search_text(text):
    """Normaliza apenas o índice de busca, preservando nomes e caminhos exibidos."""
    text = ' '.join(text.casefold().split())
    if text.isascii():
        return text
    return ''.join(char for char in unicodedata.normalize('NFKD', text)
                   if not unicodedata.combining(char))


def searchable_values(values):
    game_id = values[1]
    serial = game_id.replace('_', '-').replace('.', '')
    compact_id = serial.replace('-', '')
    return search_text(' '.join((game_id, values[2], values[6], serial, compact_id)))


class NumericTableItem(QTableWidgetItem):
    """Ordena pelo valor exato; unidades/arredondamento ficam só na apresentação."""

    def __init__(self, text, number, *, base=10):
        super().__init__(text)
        try:
            self.sort_key = (0, int(number, base))
        except ValueError:
            self.sort_key = (1, 0)  # Sem valor: depois dos números na ordem crescente.
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other):
        if isinstance(other, NumericTableItem):
            return self.sort_key < other.sort_key
        return super().__lt__(other)


@contextmanager
def updating_table(table, *, pause_sorting=False):
    """Publica mudanças em lote sem recalcular ações a cada linha/célula."""
    updates, sorting = table.updatesEnabled(), table.isSortingEnabled()
    with QSignalBlocker(table):
        table.setUpdatesEnabled(False)
        try:
            if pause_sorting:
                table.setSortingEnabled(False)
            yield
        finally:
            if pause_sorting:
                table.setSortingEnabled(sorting)
            table.setUpdatesEnabled(updates)


def status_icon(severity):
    pixmap = QPixmap(36, 36)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(STATUS_COLORS[severity][2]))
    painter.drawEllipse(1, 1, 16, 16)
    painter.setPen(QPen(QColor("white"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    if severity == "ok":
        painter.drawLine(5, 9, 8, 12)
        painter.drawLine(8, 12, 13, 6)
    elif severity == "warning":
        painter.drawLine(9, 5, 9, 10)
        painter.drawPoint(9, 13)
    else:
        painter.drawLine(6, 6, 12, 12)
        painter.drawLine(12, 6, 6, 12)
    painter.end()
    return QIcon(pixmap)


def row_status(values, is_ul):
    status = values[0]
    if is_ul:
        if status.startswith("Operação pendente"):
            return "warning", status
        if status == "Partes presentes":
            return "ok", "Partes presentes com tamanhos válidos; conteúdo não verificado por hash."
        return "error", status
    if values[1] == "—":
        return "error", status
    warnings = []
    if status != "Identificado":
        warnings.append(status)
    if "estimado" in values[3]:
        warnings.append("Mídia CD/DVD estimada pelo tamanho; confirme antes de instalar.")
    if values[8] == "Título UL precisa de ajuste":
        warnings.append(values[8])
    return ("warning", "\n".join(warnings)) if warnings else (
        "ok", "Identificação pelo SYSTEM.CNF concluída; conteúdo completo não verificado por hash.")


class StatusDelegate(QStyledItemDelegate):
    """Cores acompanham a paleta Qt e continuam visíveis na célula selecionada."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        severity = index.siblingAtColumn(0).data(STATUS_ROLE)
        if severity not in STATUS_COLORS:
            return
        foreground = status_color(severity, option.palette)
        base = option.palette.color(QPalette.ColorRole.Base)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        weight = 0.34 if selected else 0.17
        background = QColor(*[round(a * (1 - weight) + b * weight) for a, b in zip(
            base.getRgb()[:3], foreground.getRgb()[:3])])
        option.backgroundBrush = background
        option.palette.setColor(QPalette.ColorRole.HighlightedText, option.palette.color(QPalette.ColorRole.Text))
        option.palette.setColor(QPalette.ColorRole.Highlight, background)
        if selected:
            option.font.setBold(True)

    def paint(self, painter, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        icon = QIcon(styled.icon)
        severity = index.siblingAtColumn(0).data(STATUS_ROLE)
        selected = bool(styled.state & QStyle.StateFlag.State_Selected)
        if severity in STATUS_COLORS:
            # Alguns temas nativos substituem Highlight; mantém a cor da linha.
            styled.state &= ~QStyle.StateFlag.State_Selected
        if index.column() == 0:
            # O diagnóstico permanece no modelo, no tooltip e na descrição acessível.
            styled.text = ""
            styled.icon = QIcon()
        style = styled.widget.style() if styled.widget else QApplication.style()
        painter.save()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        painter.restore()
        if index.column() == 0:
            rect = QRect(0, 0, 18, 18)
            rect.moveCenter(option.rect.center())
            icon.paint(painter, rect, Qt.AlignmentFlag.AlignCenter, QIcon.Mode.Normal)
        if selected and severity in STATUS_COLORS:
            painter.save()
            painter.setPen(QPen(status_color(severity, styled.palette), 1))
            painter.drawLine(option.rect.topLeft(), option.rect.topRight())
            painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
            painter.restore()


class LogHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        # Só o campo de nível: mensagens podem conter texto arbitrário do usuário.
        level = text.partition(" [")[2].partition("]")[0]
        severity = {"WARNING": "warning", "ERROR": "error", "CRITICAL": "error"}.get(level)
        if severity:
            formatting = QTextCharFormat()
            formatting.setForeground(status_color(severity, QApplication.palette()))
            self.setFormat(0, len(text), formatting)


def size_text(size: int) -> str:
    return f"{size / (1 << 30):.2f} GiB" if size >= 1 << 30 else f"{size / (1 << 20):.2f} MiB"


def iso_rows(paths: list[str]) -> list[list[str]]:
    rows = []
    for path in paths:
        try:
            game = inspect_iso(path)
            status = game.warning or "Identificado"
            media = game.media.name + (" (estimado)" if game.media_estimated else "")
            try:
                crc = f"{usba_crc32(game.title):08X}"
            except ValueError:
                crc = "Título UL precisa de ajuste"
            rows.append([status, game.game_id, game.title, media,
                         size_text(game.size), str(game.parts), str(game.path), str(game.size),
                         crc, "ISO9660 · SYSTEM.CNF / BOOT2",
                         "Estimativa por tamanho" if game.media_estimated else f"Pasta {game.media.name}/"])
        except (OSError, ValueError) as exc:
            rows.append([str(exc), "—", Path(path).stem, "—", "—", "—", str(path)] + ["—"] * 4)
    return rows


def ul_rows(path: str) -> list[list[str]]:
    rows = []
    root = Path(path).parent
    pending = (root / TRANSACTION_DIR).exists() or (root / TRANSACTION_DIR).is_symlink()
    for record_index, entry in enumerate(read_cfg(path)):
        sizes = []
        missing = []
        invalid = []
        errors = []
        for index in range(entry.parts):
            part = root / entry.part_name(index)
            try:
                # Mesma abertura da exportação: recusa links/arquivos especiais,
                # verifica acesso e não espera por um escritor de FIFO.
                with regular_reader(part) as stream:
                    size = os.fstat(stream.fileno()).st_size
                sizes.append(size)
                if not 0 < size <= PART_SIZE or (index < entry.parts - 1 and size != PART_SIZE):
                    invalid.append(f'{index:02x} ({size} bytes)')
            except FileNotFoundError:
                missing.append(f"{index:02x}")
            except (OSError, ValueError) as exc:
                errors.append(f'Não foi possível ler a parte {index:02x} ({part.name}): {exc}')
        problems = []
        if missing:
            problems.append('Partes ausentes: ' + ', '.join(missing))
        if invalid:
            problems.append('Tamanho de parte inválido: ' + ', '.join(invalid))
        problems.extend(errors)
        if pending:
            problems.insert(0, 'Operação pendente: resolver antes de continuar')
        status = '\n'.join(problems) if problems else 'Partes presentes'
        rows.append([status, entry.game_id, entry.title, entry.media.name,
                     size_text(sum(sizes)), str(entry.parts), str(root / entry.part_name(0)),
                     str(sum(sizes)), f"{entry.crc:08X}",
                     f"ul.cfg · registro {record_index + 1} · offset 0x{record_index * 64:08X}",
                     f"ul.cfg · media=0x{entry.media:02X}"])
    return rows


def bin_rows(paths: list[str]) -> list[list[str]]:
    rows = []
    for path in paths:
        try:
            game = inspect_bin(path)
            rows.append(["Identificado (verificação parcial)", game.game_id, game.title, "CD",
                         size_text(game.raw_size), str(game.sectors), str(game.path),
                         str(game.raw_size), str(game.iso_size),
                         f"{game.sector_format} · {RAW_SECTOR} → 2048 B · payload +{game.payload_offset}",
                         str(game.bin_path)])
        except (OSError, ValueError) as exc:
            rows.append([str(exc), "—", Path(path).stem, "—", "—", "—", str(path)] + ["—"] * 4)
    return rows


def device_rows(volume):
    require_volume(volume)
    cfg, paths = library_files(volume.root)
    error = ''
    try:
        games = ul_rows(str(cfg)) if cfg else []
    except (OSError, ValueError) as exc:
        games, error = [], str(exc)
    images = iso_rows(paths)
    require_volume(volume)
    usage = shutil.disk_usage(volume.root)
    writable = volume_available(volume, write=True)
    return cfg, games, images, error, usage, writable


def volume_usage(volume):
    require_volume(volume)
    usage = shutil.disk_usage(volume.root)
    require_volume(volume)
    return usage


def run_usb_batch(jobs, volume, *, cancel, progress):
    require_volume(volume, write=True)

    def report(event):
        require_volume(volume, write=True)
        progress(event)

    return run_batch(jobs, cancel=cancel, progress=report)


def transfer_with_import_cleanup(function, args, kwargs, records, jobs, volume, delete_extracted, *, cancel, progress):
    result = function(*args, **kwargs, cancel=cancel, progress=progress)
    if isinstance(result, BatchResult) and (result.error or result.cancelled or len(result.completed) != len(result.jobs)):
        return FolderTransferResult(result, (), ())
    if jobs is None:
        jobs = (TransferJob('install', Path(args[0]), Path(args[1]), result.game_id,
                            result.title, Path(args[0]).stat().st_size, result.media),)
    progress('Verificando as cópias antes de limpar a Pasta de import…')
    guard = (lambda: require_volume(volume, write=True)) if volume else None
    removed, warnings = finish_imports(records, jobs, cancel=cancel, guard=guard, delete_extracted=delete_extracted)
    return FolderTransferResult(result, removed, warnings)


class LogSignals(QObject):
    message = Signal(str, str)


class GuiLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.signals = LogSignals()

    def emit(self, record):
        self.signals.message.emit(record.levelname, self.format(record))


class WorkerSignals(QObject):
    finished = Signal(object, str)
    progress = Signal(object)


def error_text(exc):
    return "\n".join([str(exc), *getattr(exc, "__notes__", [])])


class ScanWorker(QRunnable):
    def __init__(self, function, argument):
        super().__init__()
        self.function = function
        self.argument = argument
        self.signals = WorkerSignals()

    def run(self):
        try:
            rows = self.function(self.argument)
        except Exception as exc:
            logging.getLogger("opldeck").exception("Falha na leitura")
            self.signals.finished.emit([], error_text(exc))
        else:
            self.signals.finished.emit(rows, "")


class ConversionWorker(QRunnable):
    def __init__(self, function, args, kwargs):
        super().__init__()
        self.function, self.args, self.kwargs = function, args, kwargs
        self.signals = WorkerSignals()
        self.cancel = Event()
        self.last_emit = 0.0

    def report(self, event):
        now = time.monotonic()
        if (isinstance(event, str) or now - self.last_emit >= 0.1 or event.completed == event.total
                or getattr(event, 'phase', '') == 'sync'
                or isinstance(event, BatchProgress) and event.phase != "transfer"):
            self.signals.progress.emit(event)
            self.last_emit = now

    def run(self):
        try:
            result = self.function(*self.args, **self.kwargs, cancel=self.cancel, progress=self.report)
        except CancelledError as exc:
            self.signals.finished.emit(None, error_text(exc))
        except Exception as exc:
            logging.getLogger("opldeck").exception("Falha na operação")
            self.signals.finished.emit(None, error_text(exc))
        else:
            self.signals.finished.emit(result, "")


class InstallDialog(QDialog):
    def __init__(self, game, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ISO → UL · parâmetros de instalação")
        self.setMinimumWidth(520)
        layout = QFormLayout(self)
        self.title_edit = QLineEdit(game.title)
        self.media_combo = QComboBox()
        self.media_combo.addItems(["CD", "DVD"])
        self.media_combo.setCurrentText(game.media.name)
        layout.addRow("Título UL (1–32 caracteres ASCII)", self.title_edit)
        layout.addRow("Mídia confirmada", self.media_combo)
        self.auto_media = QCheckBox('Escolher CD/DVD automaticamente')
        layout.addRow(self.auto_media)
        self.manual_media = game.media.name
        self.auto_media.toggled.connect(self.update_media_mode)
        details = QLabel(f"ID: {game.game_id}\nTamanho: {game.size:,} bytes\n"
                         f"Partes: {game.parts} × até {PART_SIZE:,} bytes\n"
                         + (f"Estimativa experimental por tamanho: abaixo de {CD_SIZE_THRESHOLD:,} bytes = CD; "
                            "a partir desse valor = DVD. Confirme ou escolha o modo automático." if game.media_estimated
                            else f"Mídia identificada pela pasta {game.media.name}/."))
        details.setWordWrap(True)
        layout.addRow(details)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        layout.addRow(self.validation)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.game = game

    def update_media_mode(self, automatic):
        if automatic:
            self.manual_media = self.media_combo.currentText()
        self.media_combo.setCurrentText(self.game.media.name if automatic else self.manual_media)
        self.media_combo.setEnabled(not automatic)

    def accept(self):
        try:
            ULEntry(self.title_edit.text(), self.game.game_id, self.game.parts,
                    Media[self.media_combo.currentText()])
        except ValueError as exc:
            self.validation.setText(str(exc))
            return
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, *, auto_devices=True, cover_preloader=None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} · {__version__}")
        self.appearance_manager = theme_manager()
        self.resize(1250, 820)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.cover_preloader = cover_preloader if cover_preloader is not None else CoverPreloader(self)
        self._history_settings = self.cover_preloader.settings if (auto_devices or cover_preloader is not None) else None
        self.transfer_history = load_transfer_history(self._history_settings)
        self.cover_preloader.preferences_changed.connect(self.prepare_covers)
        self.cover_fallback = BackgroundFallback(self.cover_preloader, self)
        self.worker = None
        self.target = None
        self.buttons = []
        self.table_pages = {}
        self.table_controls = {}
        self.table_actions = {}
        self.convert_buttons = {}
        self.management_buttons = {}
        self.volumes = ()
        self.active_volume = None
        self.volume_ready = False
        self.pending_volume = False
        self.device_scan_timer = QTimer(self)
        self.device_scan_timer.setSingleShot(True)
        self.device_scan_timer.setInterval(250)
        self.device_scan_timer.timeout.connect(self.maybe_scan_device)
        self.device_scanning = False
        self.detached_roots = []
        self.transfer_volume = None
        self.storage_worker = None
        self.replace_scan = False
        self.imported = set()
        self.imported_bins = set()
        self.cfg_path = None
        self.pending_cfg = None
        self.operation = ""
        self.refresh_cfg = None
        self.started = 0.0
        self.transfer_rate = TransferRate()
        self.speed_drop_reported = False
        self.last_progress = None
        self.last_log_percent = -10
        self.batch_jobs = ()
        self.tracking_single = False
        self.batch_index = 0
        self.batch_started = 0.0
        self.archive_credit = {}
        self.transfer_credit = 0
        self.transfer_total = 0
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel(APP_NAME)
        font = title.font()
        font.setPointSize(22)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        layout.addWidget(QLabel("Biblioteca PlayStation 2 · ISO9660 / USBExtreme · conversão e diagnóstico"))
        self.summary = QWidget()
        summary_layout = QHBoxLayout(self.summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        self.summary_counts = {}
        for key, label in [("total", "Total de jogos"), ("ok", "Jogos íntegros*"),
                           ("warning", "Jogos com avisos"), ("error", "Jogos falhos")]:
            counter = QWidget()
            counter_layout = QHBoxLayout(counter)
            counter_layout.setContentsMargins(0, 4, 18, 4)
            counter_layout.setSpacing(6)
            counter_layout.addWidget(QLabel(label + ":"))
            number = QLabel("0")
            number_font = number.font()
            number_font.setBold(True)
            number.setFont(number_font)
            counter_layout.addWidget(number)
            counter.setToolTip("Contagem da aba atual, incluindo jogos ocultos pelos filtros.\n"
                            "Íntegros nas verificações básicas disponíveis; sem comparação por hash.")
            summary_layout.addWidget(counter)
            self.summary_counts[key] = number
        summary_layout.addStretch()
        layout.addWidget(self.summary)
        device_row = QHBoxLayout()
        device_row.addWidget(QLabel('Pendrive:'))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(280)
        self.device_combo.addItem('Aguardando pendrive…', None)
        self.device_combo.currentIndexChanged.connect(self.select_device)
        device_row.addWidget(self.device_combo, 1)
        self.device_loading = QLabel()
        self.device_loading.setMinimumWidth(20)
        self.device_loading.setAccessibleName('Lendo pendrive')
        device_row.addWidget(self.device_loading)
        self.device_frame = 0
        self.device_animation = QTimer(self)
        self.device_animation.setInterval(120)
        self.device_animation.timeout.connect(self.animate_device)
        self.device_rescan = QPushButton('Reler pendrive')
        self.device_rescan.clicked.connect(self.rescan_device)
        device_row.addWidget(self.device_rescan)
        layout.addLayout(device_row)
        self.device_status = QLabel('Conecte um pendrive. A leitura começa quando o Linux montar o dispositivo.')
        self.device_status.setTextFormat(Qt.TextFormat.PlainText)
        self.device_status.setWordWrap(True)
        layout.addWidget(self.device_status)
        storage_row = QHBoxLayout()
        self.storage_bar = QProgressBar()
        self.storage_bar.setRange(0, 1000)
        self.storage_bar.setTextVisible(False)
        self.storage_bar.setFixedSize(150, 14)
        self.storage_bar.setAccessibleName('Ocupação do pendrive')
        self.storage_label = QLabel('Armazenamento: aguardando pendrive.')
        self.storage_label.setTextFormat(Qt.TextFormat.PlainText)
        storage_row.addWidget(self.storage_bar)
        storage_row.addWidget(self.storage_label, 1)
        layout.addLayout(storage_row)
        self.storage_bar.setEnabled(False)
        self.storage_bar.setValue(0)
        self.storage_timer = QTimer(self)
        self.storage_timer.setInterval(3000)
        self.storage_timer.timeout.connect(self.refresh_storage)
        self.storage_timer.start()
        self.activity = ActivityPanel()
        layout.addWidget(self.activity)
        self.tabs = QTabWidget()
        self.iso_table = self._page("Imagens ISO", "Adicionar ISOs…", self.choose_isos,
                                   "Adicione imagens para ler o ID em SYSTEM.CNF. CD/DVD fora dessas pastas é uma estimativa.",
                                   "Converter seleção para UL…", self.choose_install)
        self.zip_button = QPushButton('Importar ZIP/7z…')
        self.zip_button.clicked.connect(self.choose_archives)
        self.table_pages[self.iso_table].layout().itemAt(1).layout().insertWidget(1, self.zip_button)
        self.buttons.append(self.zip_button)
        self.import_search_button = QPushButton('Buscar imports')
        self.import_search_button.setEnabled(False)
        self.import_search_button.clicked.connect(lambda: self.import_monitor.search())
        self.table_pages[self.iso_table].layout().itemAt(1).layout().insertWidget(2, self.import_search_button)
        self.buttons.append(self.import_search_button)
        self.usb_start = self.convert_buttons[self.iso_table]
        self.usb_start.setText('Iniciar → pendrive')
        self.usb_start.clicked.disconnect()
        self.usb_start.clicked.connect(self.start_usb_install)
        self.usb_start.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        self.ul_table = self._page("Jogos UL", "Abrir ul.cfg…", self.choose_cfg,
                                  "Consulte o índice e verifique presença e tamanho das partes antes de exportar.",
                                  "Exportar seleção para ISO…", self.choose_export)
        management_row = QHBoxLayout()
        for label, callback in [("Renomear seleção…", self.choose_rename),
                                ("Excluir seleção…", self.choose_delete),
                                ("Resolver operação pendente…", self.choose_recovery),
                                ("Reconstruir ul.cfg…", self.choose_rebuild)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            if label.startswith('Excluir'):
                self.management_buttons['delete'] = button
            elif label.startswith('Renomear'):
                self.management_buttons['rename'] = button
            management_row.addWidget(button)
            self.buttons.append(button)
        management_row.addStretch()
        self.table_pages[self.ul_table].layout().insertLayout(2, management_row)
        self.bin_table = self._page("Imagens BIN/CUE", "Adicionar BIN/CUE…", self.choose_bins,
                                   "PS2 · faixa única raw de 2352 bytes · Mode 1 ou Mode 2 Form 1. "
                                   "Todos os setores são verificados na conversão; EDC/ECC não são verificados.",
                                   "Converter seleção para ISO…", self.choose_bin_conversion, headers=BIN_HEADERS)
        self.transfer_queue = TransferQueue(self)
        self.transfer_queue.retry_requested.connect(self.retry_batch)
        self.transfer_queue.cancel_requested.connect(self.cancel_conversion)
        self.tabs.addTab(self.transfer_queue, 'Fila de transferências')
        self._log_page()
        self.downloads_page = QWidget()
        downloads_layout = QVBoxLayout(self.downloads_page)
        downloads_layout.setContentsMargins(32, 32, 32, 32)
        downloads_layout.addStretch()
        downloads_title = QLabel('Download de jogos')
        downloads_font = downloads_title.font()
        downloads_font.setPointSize(20)
        downloads_font.setBold(True)
        downloads_title.setFont(downloads_font)
        downloads_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        downloads_layout.addWidget(downloads_title)
        self.downloads_status = QLabel('Em breve')
        self.downloads_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        downloads_layout.addWidget(self.downloads_status)
        downloads_hint = QLabel('Este recurso estará disponível em uma próxima atualização.')
        downloads_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        downloads_hint.setWordWrap(True)
        downloads_layout.addWidget(downloads_hint)
        downloads_layout.addStretch()
        self.tabs.insertTab(0, self.downloads_page, 'Downloads')
        self.about_page = AboutPage(self)
        self.tabs.addTab(self.about_page, "Sobre")
        layout.addWidget(self.tabs, 1)
        self.status_icons = {severity: status_icon(severity) for severity in STATUS_LABELS}
        scope = QLabel("* Íntegros nas verificações básicas disponíveis; sem comparação por hash. Passe o mouse no ícone para ver o diagnóstico.")
        scope.setWordWrap(True)
        layout.addWidget(scope)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(110)
        self.details.setPlaceholderText("Selecione um arquivo para consultar caminhos completos, bytes, CRC e origem dos metadados.")
        self.details_toggle = QPushButton('Mostrar detalhes técnicos')
        self.details_toggle.setCheckable(True)
        layout.addWidget(self.details_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.technical_panel = QWidget()
        technical_layout = QVBoxLayout(self.technical_panel)
        technical_layout.setContentsMargins(0, 0, 0, 0)
        technical_layout.addWidget(self.details)
        self.technical_panel.hide()
        layout.addWidget(self.technical_panel)
        self.details_toggle.toggled.connect(self.technical_panel.setVisible)
        self.details_toggle.toggled.connect(lambda checked: self.details_toggle.setText(
            'Ocultar detalhes técnicos' if checked else 'Mostrar detalhes técnicos'))
        self.operation_progress = QWidget()
        progress_layout = QVBoxLayout(self.operation_progress)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.activity.content_layout.addWidget(self.operation_progress)
        self.operation_progress.hide()
        self.batch_label = QLabel()
        self.batch_label.setTextFormat(Qt.TextFormat.PlainText)
        self.batch_label.setWordWrap(True)
        self.batch_label.hide()
        progress_layout.addWidget(self.batch_label)
        self.overall_progress = OperationProgressBar()
        self.overall_progress.setRange(0, 1000)
        self.overall_progress.setValue(0)
        self.overall_progress.setFormat("Total do lote: %p%")
        self.overall_progress.hide()
        self.overall_progress.setMinimumHeight(22)
        self.overall_progress.setAccessibleName('Progresso total do lote')
        progress_layout.addWidget(self.overall_progress)
        progress_row = QHBoxLayout()
        self.progress_bar = OperationProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setMinimumHeight(22)
        self.progress_bar.setAccessibleName('Progresso do jogo atual')
        self.cancel_button = QPushButton("Cancelar operação")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_conversion)
        progress_row.addWidget(self.progress_bar)
        progress_row.addWidget(self.cancel_button)
        progress_layout.addLayout(progress_row)
        self.progress_metrics = QLabel()
        self.progress_metrics.setTextFormat(Qt.TextFormat.PlainText)
        self.progress_metrics.setWordWrap(True)
        progress_layout.addWidget(self.progress_metrics)
        self.transfer_details = QLabel(f"Buffer: {BUFFER_SIZE:,} bytes · parte UL: {PART_SIZE:,} bytes · aguardando operação")
        self.transfer_details.setWordWrap(True)
        self.transfer_details.setTextFormat(Qt.TextFormat.PlainText)
        technical_layout.addWidget(self.transfer_details)
        self.setCentralWidget(central)
        self._setup_actions()
        self.tabs.currentChanged.connect(self.update_summary)
        self.update_summary()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.update_transfer)
        self.log_handler = GuiLogHandler()
        self.log_handler.signals.message.connect(self.log)
        self.app_logger = logging.getLogger("opldeck")
        self.app_logger.setLevel(logging.INFO)
        self.app_logger.addHandler(self.log_handler)
        self.log("INFO", f"{APP_NAME} {__version__} iniciado | Qt/PySide6 | buffer={BUFFER_SIZE} | parte_UL={PART_SIZE}")
        self.statusBar().showMessage("Pronto · selecione ISOs ou abra uma lista ul.cfg")
        self.device_monitor = DeviceMonitor(self)
        self.device_monitor.changed.connect(self.devices_changed)
        self.device_monitor.failed.connect(self.device_error)
        self.import_monitor = ImportMonitor(self, ScanWorker, self.cover_preloader.settings)
        self.import_monitor.changed.connect(self.update_import_search)
        self.archive_import = ArchiveImportController(self, ScanWorker)
        self.appearance_manager.changed.connect(self.update_appearance)
        self.update_appearance()
        self.update_import_search()
        if auto_devices:
            self.device_monitor.start()

    def animate_device(self):
        self.device_frame = (self.device_frame + 1) % 4
        self.device_loading.setText(('◴', '◷', '◶', '◵')[self.device_frame])

    def update_appearance(self, *_):
        for table in (self.iso_table, self.ul_table, self.bin_table):
            table.verticalHeader().setDefaultSectionSize(self.appearance_manager.row_height(table.fontMetrics()))

    def devices_changed(self, volumes, unmounted=0):
        self.volumes = tuple(volumes)
        current = self.active_volume
        selected = next((v for v in volumes if current and v.key == current.key), None)
        if self.transfer_volume and not any(v.key == self.transfer_volume.key for v in volumes):
            if isinstance(self.worker, ConversionWorker):
                self.worker.cancel.set()
                self.activity.cancel_pending()
                self.activity.message.setText('Pendrive removido. Aguarde a confirmação da interrupção da transferência.')
                self.cancel_button.setEnabled(False)
            self.log('WARNING', 'Pendrive de destino removido; interrompendo a transferência.')
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem('Escolha o pendrive…' if volumes else 'Aguardando pendrive…', None)
        for volume in volumes:
            self.device_combo.addItem(f'{volume.label} · {volume.root}' + (' · somente leitura' if volume.readonly else ''), volume)
        if selected:
            self.device_combo.setCurrentIndex(next(i + 1 for i, v in enumerate(volumes) if v.key == selected.key))
        elif len(volumes) == 1:
            self.device_combo.setCurrentIndex(1)
            selected = volumes[0]
        self.device_combo.blockSignals(False)
        if selected != current:
            self.select_device()
        elif selected and not self.volume_ready and not self.device_scanning and not self.pending_volume:
            self.rescan_device()
        if not selected:
            self.device_status.setText('Pendrive detectado, aguardando montagem pelo Linux. Abra-o no gerenciador de arquivos.'
                                       if unmounted and not volumes else
                                       'Escolha o pendrive de destino.' if volumes else 'Aguardando pendrive conectado e montado.')
            if unmounted and not volumes:
                self.device_animation.start()
                self.animate_device()
            elif not self.device_scanning:
                self.device_animation.stop()
                self.device_loading.clear()
        self._update_actions()

    def device_error(self, message):
        self.cover_preloader.reset()
        self.volume_ready = False
        self.clear_storage()
        self.device_status.setText(message)
        self.log('WARNING', message)
        self._update_actions()

    def select_device(self, *_):
        previous = self.active_volume
        self.active_volume = self.device_combo.currentData()
        self.cover_preloader.reset(self.active_volume)
        self.clear_storage()
        self.volume_ready = False
        self.pending_volume = self.active_volume is not None
        if previous and (not self.active_volume or previous.key != self.active_volume.key):
            self.detached_roots.append(previous.root)
        if self.active_volume:
            self.device_status.setText(f'Lendo pendrive: {self.active_volume.root}…')
            self.device_animation.start()
            self.animate_device()
        else:
            self.device_animation.stop()
            self.device_loading.clear()
            self.device_status.setText('Pendrive desconectado. Aguardando um destino.')
        self.clean_detached_rows()
        self._update_actions()
        self.maybe_scan_device()

    def clean_detached_rows(self):
        if self.worker is not None or not self.detached_roots:
            return
        with updating_table(self.iso_table), updating_table(self.ul_table):
            for root in self.detached_roots:
                for row in reversed(range(self.iso_table.rowCount())):
                    path = self.iso_table.item(row, 6).text()
                    if Path(path).is_relative_to(root):
                        self.imported.discard(path)
                        self.iso_table.removeRow(row)
                if self.cfg_path and self.cfg_path.parent == root:
                    self.ul_table.setRowCount(0)
                    self.cfg_path = None
        self.detached_roots.clear()
        self.filter_table(self.iso_table)
        self.filter_table(self.ul_table)

    def rescan_device(self):
        if self.active_volume and self.worker is None:
            self.cover_preloader.reset(self.active_volume)
            self.volume_ready = False
            self.pending_volume = True
            self.maybe_scan_device()
        elif not self.active_volume:
            self.device_monitor.poll()

    def maybe_scan_device(self):
        if self.worker is not None:
            return
        # Modal confirmations run the event loop too. Do not let USB discovery
        # occupy the operation slot while a transfer is being confirmed.
        if QApplication.activeModalWidget() is not None:
            if self.pending_volume or self.detached_roots:
                self.device_scan_timer.start()
            return
        self.device_scan_timer.stop()
        self.clean_detached_rows()
        if not self.pending_volume or self.active_volume is None:
            return
        volume = self.active_volume
        self.pending_volume = False
        self.device_scanning = True
        self.volume_ready = False
        self.device_status.setText(f'Lendo ul.cfg e imagens ISO de {volume.label}…')
        self.device_animation.start()
        self.animate_device()
        self.worker = ScanWorker(device_rows, volume)
        self.activity.begin('Lendo pendrive…', f'{volume.label} · procurando ul.cfg e imagens ISO.', scan=True)
        self.worker.signals.finished.connect(lambda result, error: self.device_read(volume, result, error))
        self._busy(True)
        self.pool.start(self.worker)

    def device_read(self, volume, result, error):
        self.worker = None
        self.device_scanning = False
        self.device_animation.stop()
        self.device_loading.clear()
        if self.active_volume is None or self.active_volume.key != volume.key or not volume_available(volume):
            self.activity.finish_scan('Pendrive desconectado ou substituído durante a leitura.', error=True)
            self._busy(False)
            if self.active_volume and self.active_volume.key == volume.key:
                self.volume_ready = False
                self.device_status.setText('Pendrive removido durante a leitura. Reconecte para continuar.')
            return
        if error:
            self.device_status.setText('Falha ao ler o pendrive: ' + error)
            self.clear_storage()
            self.activity.finish_scan(error, error=True)
            self.log('ERROR', error)
        else:
            cfg, games, images, cfg_error, usage, writable = result
            self.show_storage(usage)
            had_local = any(not Path(self.iso_table.item(r, 6).text()).is_relative_to(volume.root)
                            for r in range(self.iso_table.rowCount()))
            # Releitura substitui somente as linhas do dispositivo, preservando a fila local.
            self.detached_roots.append(volume.root)
            self.clean_detached_rows()
            self.cfg_path = self.pending_cfg = cfg
            self.ul_table.setRowCount(0)
            self.append_rows(self.ul_table, games)
            self.imported.update(row[6] for row in images)
            self.append_rows(self.iso_table, images)
            self.filter_table(self.iso_table)
            self.filter_table(self.ul_table)
            self.volume_ready = writable and not cfg_error and (cfg is None or cfg.name == 'ul.cfg')
            message = f'{volume.label} · {len(games)} jogos UL · {len(images)} ISOs'
            if cfg_error:
                message += ' · Falha em ul.cfg: ' + cfg_error
            elif not writable:
                message += ' · Somente leitura: transferência bloqueada'
            elif cfg and cfg.name != 'ul.cfg':
                message += ' · Para gravar, o índice precisa se chamar ul.cfg'
            else:
                message += ' · Destino pronto'
            self.device_status.setText(message)
            self.activity.finish_scan(message, error=bool(cfg_error))
            self.statusBar().showMessage('Leitura do pendrive concluída.')
            self.log('WARNING' if cfg_error else 'INFO', message)
            self.show_table(self.iso_table if had_local or not cfg else self.ul_table)
            self.prepare_covers()
        self._busy(False)

    def prepare_covers(self):
        if self.active_volume is None:
            return
        keys = []
        for table, kind in ((self.ul_table, 'UL'), (self.iso_table, 'ISO')):
            for row in range(table.rowCount()):
                game_id = table.item(row, 1).text()
                path = table.item(row, 6).text()
                root = library_root(path, kind) or self.active_volume.root
                try:
                    keys.append(CoverKey.make(root, game_id, table.item(row, 2).text(), Path(path).stem))
                except ValueError:
                    continue
        self.cover_preloader.preload(keys)

    def cover_preferences_saved(self):
        self.cover_preloader.sync_preferences()
        self.import_monitor.reset()

    def open_settings(self, tab='appearance'):
        dialog = SettingsDialog(self, initial_tab=tab)
        dialog.saved.connect(self.cover_preferences_saved)
        dialog.exec()

    def clear_storage(self):
        self.storage_bar.setValue(0)
        self.storage_bar.setEnabled(False)
        self.storage_label.setText('Armazenamento: aguardando leitura do pendrive.')
        self.storage_bar.setAccessibleDescription('Armazenamento indisponível')

    def show_storage(self, usage):
        self.storage_bar.setEnabled(True)
        self.storage_bar.setValue(usage.used * 1000 // usage.total if usage.total else 0)
        text = f'{size_text(usage.free)} livres · {size_text(usage.used)} usados · {size_text(usage.total)} no total'
        self.storage_label.setText(text)
        self.storage_bar.setAccessibleDescription(text)

    def refresh_storage(self):
        if self.worker is not None or self.storage_worker is not None or self.active_volume is None:
            return
        volume = self.active_volume
        self.storage_worker = ScanWorker(volume_usage, volume)
        self.storage_worker.signals.finished.connect(lambda usage, error: self.storage_finished(volume, usage, error))
        self.pool.start(self.storage_worker)

    def storage_finished(self, volume, usage, error):
        self.storage_worker = None
        if not self.active_volume or self.active_volume.key != volume.key:
            return
        if error or not volume_available(volume):
            self.clear_storage()
        else:
            self.show_storage(usage)

    def usb_values(self):
        return [[self.iso_table.item(row, col).text() for col in range(self.iso_table.columnCount())]
                for row in self.usb_rows()]

    def usb_rows(self):
        rows = sorted(self._selected_rows(self.iso_table))
        if not rows:
            rows = range(self.iso_table.rowCount())
        for row in rows:
            if not self.iso_table.isRowHidden(row) and (not self.active_volume or not Path(
                    self.iso_table.item(row, 6).text()).is_relative_to(self.active_volume.root)):
                yield row

    def usb_ready(self):
        if self.worker is not None or not self.volume_ready:
            return False
        found = False
        for row in self.usb_rows():
            if self.iso_table.item(row, 1).text() == '—':
                return False
            found = True
        return found

    def start_usb_install(self):
        if self.worker is not None or not self.volume_ready or self.active_volume is None:
            return
        volume = self.active_volume
        values = self.usb_values()
        if not values or any(row[1] == '—' for row in values):
            return
        if not volume_available(volume, write=True):
            self.device_error('O pendrive não está disponível para gravação. Aguarde uma nova leitura.')
            return
        dialog = BatchDialog(values, 'install', parent=self, volume=volume)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                require_volume(volume, write=True)
                jobs = dialog.jobs()
            except (OSError, ValueError) as exc:
                self.show_error('Destino indisponível', str(exc))
                return
            self.start_batch(jobs, volume=volume)

    def _page(self, name, button_text, callback, description, convert_text, convert_callback, headers=HEADERS):
        page = QWidget()
        layout = QVBoxLayout(page)
        hint = QLabel(description + "\nUse Ctrl/Shift para selecionar vários jogos e transferir em lote.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        toolbar = QHBoxLayout()
        add = QPushButton(button_text)
        add.clicked.connect(callback)
        clear = QPushButton("Limpar lista")
        convert = QPushButton(convert_text)
        convert.clicked.connect(convert_callback)
        toolbar.addWidget(add)
        toolbar.addWidget(convert)
        toolbar.addWidget(clear)
        refresh = QPushButton("Atualizar · F5")
        toolbar.addWidget(refresh)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        table = QTableWidget(0, len(headers))
        table.setItemDelegate(StatusDelegate(table))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setToolTip("Use Ctrl ou Shift para selecionar vários jogos e transferir em lote.")
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setShowGrid(False)
        table.verticalHeader().setDefaultSectionSize(34)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for index, width in enumerate([58, 115, 210, 110, 90, 65, 200, 115, 140, 290, 210]):
            table.setColumnWidth(index, width)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setSortingEnabled(True)
        table.itemSelectionChanged.connect(lambda: self.show_details(table))
        clear.clicked.connect(lambda: self.clear_table(table))
        refresh.clicked.connect(lambda: self.refresh_table(table))
        search_row = QHBoxLayout()
        search = QLineEdit()
        search.setPlaceholderText("Buscar título, ID ou caminho…  Ctrl+F")
        search.setToolTip('Busque por trecho do título, ID ou caminho, com ou sem acentos. '
                          'IDs aceitam SLUS_123.45, SLUS-12345 ou SLUS12345.')
        search.setClearButtonEnabled(True)
        status_filter = QComboBox()
        for text, value in [("Todos os jogos", "all"), ("Íntegros*", "ok"), ("Com avisos", "warning"), ("Com falhas", "error")]:
            status_filter.addItem(text, value)
        visible_count = QLabel("0 jogos")
        search_row.addWidget(search, 1)
        search_row.addWidget(status_filter)
        search_row.addWidget(visible_count)
        layout.addLayout(search_row)
        self.table_controls[table] = (search, status_filter, visible_count)
        self.convert_buttons[table] = convert
        search.textChanged.connect(lambda: self.filter_table(table))
        status_filter.currentIndexChanged.connect(lambda: self.filter_table(table))
        layout.addWidget(table)
        self.buttons.extend([add, clear, convert, refresh])
        self.table_pages[table] = page
        self.tabs.addTab(page, name)
        return table

    def show_table(self, table):
        self.tabs.setCurrentWidget(self.table_pages[table])

    def active_table(self):
        page = self.tabs.currentWidget()
        return next((table for table, candidate in self.table_pages.items() if candidate is page), None)

    def update_summary(self, *_):
        table = self.active_table()
        self.summary.setVisible(table is not None)
        if table is None:
            self.details.clear()
            return
        counts = {"total": table.rowCount(), "ok": 0, "warning": 0, "error": 0}
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and item.data(STATUS_ROLE) in STATUS_LABELS:
                counts[item.data(STATUS_ROLE)] += 1
        for key, value in counts.items():
            self.summary_counts[key].setText(str(value))
        self.show_details(table)

    def filter_table(self, table):
        search, status_filter, label = self.table_controls[table]
        query, severity = search_text(search.text()), status_filter.currentData()
        visible = 0
        with updating_table(table):
            for row in range(table.rowCount()):
                status = table.item(row, 0)
                indexed = status.data(SEARCH_ROLE)
                match = (not query or query in indexed) and (
                    severity == "all" or status.data(STATUS_ROLE) == severity)
                if table.isRowHidden(row) == match:
                    table.setRowHidden(row, not match)
                visible += match
            if table.currentRow() >= 0 and table.isRowHidden(table.currentRow()):
                table.clearSelection()
                table.setCurrentItem(None)
                if table is self.active_table():
                    self.details.clear()
        label.setText(f"{visible} de {table.rowCount()} jogos")
        self.update_summary()
        self._update_actions()

    def choose_cover_batch(self, table):
        if self.worker is not None or table not in (self.iso_table, self.ul_table):
            return
        values = self._selected_values(table)
        if not values:
            return
        kind = 'UL' if table is self.ul_table else 'ISO'
        fallback = self.active_volume.root if self.active_volume else None
        if fallback is None and any(library_root(value[6], kind) is None for value in values):
            folder = QFileDialog.getExistingDirectory(self, 'Biblioteca de destino das capas (pasta que contém ART)')
            if not folder:
                return
            fallback = Path(folder)
        keys = []
        for value in values:
            try:
                keys.append(CoverKey.make(library_root(value[6], kind) or fallback, value[1], value[2], Path(value[6]).stem))
            except ValueError:
                continue
        if not keys:
            self.statusBar().showMessage('Selecione jogos com ID identificado para preparar as capas.')
            return
        dialog = CoverBatchDialog(keys, self, settings=self.cover_preloader.settings, volume=self.active_volume)
        paused = self.cover_preloader.paused
        self.cover_preloader.set_paused(True)
        self.cover_preloader.invalidated.connect(dialog.invalidate)
        try:
            dialog.exec()
        finally:
            self.cover_preloader.invalidated.disconnect(dialog.invalidate)
            for key in keys:
                self.cover_preloader.invalidate_game(key.root, key.game_id)
            self.cover_preloader.set_paused(paused)
            if dialog.saved_paths:
                self.log('INFO', f'Capas em lote: {len(dialog.saved_paths)} salvas.')
            dialog.deleteLater()

    def duplicate_groups(self):
        games = []
        for table, kind in ((self.iso_table, 'ISO'), (self.ul_table, 'UL')):
            for row in range(table.rowCount()):
                record = re.search(r'· registro (\d+) ·', table.item(row, 9).text()) if kind == 'UL' else None
                games.append(GameOccurrence(table.item(row, 1).text(), table.item(row, 2).text(), kind,
                                            Path(table.item(row, 6).text()), int(record[1]) - 1 if record else row))
        return duplicate_games(games)

    def show_duplicates(self):
        DuplicatesDialog(self.duplicate_groups(), self).exec()

    def show_transfer_history(self):
        TransferHistoryDialog(self.transfer_history, on_clear=self.clear_transfer_history, parent=self).exec()

    def clear_transfer_history(self):
        self.transfer_history.clear()
        save_transfer_history(self._history_settings, self.transfer_history)

    def _record_transfer_history(self, items):
        if not items:
            return
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        for game_id, title, operation, source, destination in items:
            self.transfer_history.append(
                TransferHistoryEntry(stamp, str(game_id or "—"), str(title), str(operation), str(source), str(destination))
            )
        save_transfer_history(self._history_settings, self.transfer_history)

    def _setup_actions(self):
        library = self.menuBar().addMenu('Biblioteca')
        self.duplicates_action = library.addAction('Detectar jogos duplicados…')
        self.duplicates_action.triggered.connect(self.show_duplicates)
        self.save_covers_action = library.addAction('Salvar capas da seleção em lote…')
        self.save_covers_action.triggered.connect(lambda: self.choose_cover_batch(self.active_table()))
        queue_action = library.addAction('Fila de transferências')
        queue_action.triggered.connect(lambda: self.tabs.setCurrentWidget(self.transfer_queue))
        settings = self.menuBar().addMenu("Configurações")
        self.appearance_action = settings.addAction("Aparência…")
        self.appearance_action.triggered.connect(lambda: self.open_settings())
        self.providers_action = settings.addAction("Provedores…")
        self.providers_action.triggered.connect(lambda: self.open_settings('providers'))
        self.import_settings_action = settings.addAction('Pasta de import…')
        self.import_settings_action.triggered.connect(lambda: self.open_settings('import'))
        self.import_retry_action = settings.addAction('Rever pasta de import')
        self.import_retry_action.triggered.connect(lambda: self.import_monitor.reset())
        self.history_action = self.menuBar().addAction("Histórico")
        self.history_action.triggered.connect(self.show_transfer_history)
        for table, add, convert, convert_label in [
            (self.iso_table, self.choose_isos, self.choose_install, "Converter para UL…"),
            (self.ul_table, self.choose_cfg, self.choose_export, "Exportar para ISO…"),
            (self.bin_table, self.choose_bins, self.choose_bin_conversion, "Converter para ISO…"),
        ]:
            actions = {}

            def add_action(key, label, callback, shortcut=None):
                action = QAction(label, table)
                action.triggered.connect(callback)
                if shortcut:
                    action.setShortcut(QKeySequence(shortcut))
                    action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                table.addAction(action)
                actions[key] = action

            add_action("add", "Abrir ul.cfg…" if table is self.ul_table else "Adicionar arquivos…", add)
            add_action("convert", convert_label, convert, "Ctrl+Return")
            if table is self.ul_table:
                add_action("rename", "Renomear jogo…", self.choose_rename, "F2")
                add_action("delete", "Excluir jogo do disco…", self.choose_delete, "Delete")
                add_action("rebuild", "Reconstruir ul.cfg…", self.choose_rebuild)
            else:
                add_action("remove", "Remover da lista (manter arquivo)", lambda checked=False, t=table: self.remove_selected(t), "Delete")
            if table is not self.bin_table:
                add_action('covers', 'Salvar capas da seleção em lote…', lambda checked=False, t=table: self.choose_cover_batch(t))
            add_action("details", "Ficha do jogo…", lambda checked=False, t=table: self.show_details_dialog(t), "Return")
            add_action("folder", "Abrir pasta do jogo", lambda checked=False, t=table: self.open_folder(t), "Ctrl+Shift+O")
            add_action("copy_id", "Copiar ID", lambda checked=False, t=table: self.copy_selected(t, 1), "Ctrl+Shift+C")
            add_action("copy_path", "Copiar caminho", lambda checked=False, t=table: self.copy_selected(t, 6), "Ctrl+Shift+P")
            add_action("refresh", "Atualizar lista", lambda checked=False, t=table: self.refresh_table(t))
            add_action("clear", "Limpar lista (manter arquivos)", lambda checked=False, t=table: self.clear_table(t))
            self.table_actions[table] = actions
            table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            table.customContextMenuRequested.connect(lambda position, t=table: self.show_context_menu(t, position))
            table.itemSelectionChanged.connect(self._update_actions)
            table.itemDoubleClicked.connect(lambda item, t=table: self.show_details_dialog(t))
        self.window_actions = []
        for shortcut, callback in [("Ctrl+O", self.open_current), ("Ctrl+F", self.focus_search),
                                   ("F5", lambda: self.refresh_table(self.active_table()))]:
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            self.addAction(action)
            self.window_actions.append(action)
        self._update_actions()

    def _update_actions(self):
        busy = self.worker is not None
        for table, actions in self.table_actions.items():
            rows = self._selected_rows(table)
            selected = len(rows) == 1
            row = rows[0] if selected else -1
            identified = selected and table.item(row, 1) is not None and table.item(row, 1).text() != "—"
            manageable = identified and self.cfg_path is not None and not table.item(row, 0).text().startswith('Operação pendente')
            deletable = bool(rows) and self.cfg_path is not None and all(
                table.item(r, 1) is not None and table.item(r, 1).text() != '—'
                and not table.item(r, 0).text().startswith('Operação pendente') for r in rows)
            batch_usable = bool(rows) and all(table.item(r, 1).text() != "—" and
                (table is not self.ul_table or table.item(r, 0).data(STATUS_ROLE) == "ok") for r in rows)
            for key, action in actions.items():
                enabled = {"add": not busy, "rebuild": not busy, "refresh": not busy and (table.rowCount() > 0 or
                           (table is self.ul_table and self.cfg_path is not None)),
                           "clear": not busy and table.rowCount() > 0,
                           "covers": not busy and bool(rows),
                           "convert": not busy and batch_usable, "rename": not busy and manageable,
                           "delete": not busy and deletable, "remove": not busy and bool(rows),
                           "details": selected, "folder": selected, "copy_id": identified,
                           "copy_path": selected}[key]
                action.setEnabled(enabled)
            if table is not self.iso_table:
                self.convert_buttons[table].setEnabled(actions['convert'].isEnabled())
            if table is self.ul_table:
                actions['delete'].setText(f'Excluir {len(rows)} jogos selecionados…' if len(rows) > 1 else 'Excluir jogo do disco…')
                for key, button in self.management_buttons.items():
                    button.setEnabled(actions[key].isEnabled())
        self.save_covers_action.setEnabled(not busy and self.active_table() in (self.iso_table, self.ul_table)
                                           and bool(self._selected_rows(self.active_table())))
        self.duplicates_action.setEnabled(not busy)
        if hasattr(self, 'usb_start'):
            ready = self.usb_ready()
            self.usb_start.setEnabled(ready)
            self.usb_start.setToolTip('Revisar e transferir as ISOs selecionadas (ou todas as visíveis) para o pendrive.'
                                     if ready else 'Adicione ISOs locais e aguarde a leitura de um pendrive gravável.')
            self.device_combo.setEnabled(not busy)
            self.device_rescan.setEnabled(not busy)

    def show_context_menu(self, table, position):
        index = table.indexAt(position)
        if index.isValid():
            if not table.selectionModel().isRowSelected(index.row()):
                table.clearSelection()
                table.selectRow(index.row())
        else:
            table.clearSelection()
            table.setCurrentItem(None)
        self._update_actions()
        menu = QMenu(table)
        actions = self.table_actions[table]
        for group in [("add", "convert", "rename", "delete", "remove"),
                      ("details", "covers", "folder", "copy_id", "copy_path"), ("refresh", "rebuild", "clear")]:
            if menu.actions():
                menu.addSeparator()
            for key in group:
                if key in actions:
                    menu.addAction(actions[key])
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(table.viewport().mapToGlobal(position))

    def open_current(self):
        table = self.active_table()
        if table is not None and self.worker is None:
            self.table_actions[table]["add"].trigger()

    def focus_search(self):
        table = self.active_table()
        if table is not None:
            search = self.table_controls[table][0]
            search.setFocus()
            search.selectAll()

    def copy_selected(self, table, column):
        selected = self._selected(table)
        if selected:
            QApplication.clipboard().setText(selected[column])
            self.statusBar().showMessage("ID copiado." if column == 1 else "Caminho copiado.", 3000)

    def open_folder(self, table):
        selected = self._selected(table)
        if selected:
            directory = Path(selected[6]).parent
            if not directory.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
                self.show_error("Abrir pasta", f"Não foi possível abrir a pasta: {directory}")

    def show_details_dialog(self, table):
        selected = self._selected(table)
        if not selected:
            return
        self.show_details(table)
        self._update_actions()
        actions = self.table_actions[table]
        volume = self.active_volume

        def guard(root, write):
            if volume and Path(root) == volume.root:
                require_volume(volume, write=write)

        dialog = GameDialog(
            selected, "UL" if table is self.ul_table else "ISO" if table is self.iso_table else "BIN/CUE",
            self.details.toPlainText(), editable=self.worker is None,
            can_rename="rename" in actions and actions["rename"].isEnabled(),
            can_delete="delete" in actions and actions["delete"].isEnabled(),
            can_convert=actions["convert"].isEnabled(), parent=self,
            library=volume.root if volume else None, library_guard=guard,
            preloader=self.cover_preloader,
        )
        dialog.exec()
        for path in dialog.saved_paths:
            self.log("INFO", f"Capa salva | ID={selected[1]} | destino={path}")
        if dialog.requested_action:
            actions[dialog.requested_action].trigger()

    def remove_selected(self, table):
        if self.worker is not None or table is self.ul_table:
            return
        rows = [i.row() for i in table.selectionModel().selectedRows() if not table.isRowHidden(i.row())]
        if rows:
            paths = self.imported if table is self.iso_table else self.imported_bins
            with updating_table(table):
                for row in sorted(rows, reverse=True):
                    paths.discard(table.item(row, 6).text())
                    table.removeRow(row)
            self.details.clear()
            self.filter_table(table)
            self.statusBar().showMessage(f"{len(rows)} jogo(s) removido(s) da lista; arquivos mantidos no disco.")

    def refresh_table(self, table):
        if table is None or self.worker is not None:
            return
        self.cover_preloader.reset(self.active_volume)
        if table is self.ul_table:
            if self.cfg_path:
                self.load_cfg(self.cfg_path)
        else:
            paths = [table.item(row, 6).text() for row in range(table.rowCount())]
            if paths:
                self._scan(iso_rows if table is self.iso_table else bin_rows, paths, table, replace=True)

    def _log_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        save = QPushButton("Salvar log…")
        save.clicked.connect(self.save_log)
        clear = QPushButton("Limpar log")
        clear.clicked.connect(lambda: self.log_view.clear())
        copy = QPushButton("Copiar Log")
        copy.clicked.connect(self.copy_log)
        self.copy_log_button = copy
        row.addWidget(save)
        row.addWidget(clear)
        row.addWidget(copy)
        row.addStretch()
        row.addWidget(QLabel("Horário · nível · evento | últimas 10.000 linhas"))
        layout.addLayout(row)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(10_000)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_highlighter = LogHighlighter(self.log_view.document())
        layout.addWidget(self.log_view)
        self.tabs.addTab(page, "Log")

    def copy_log(self):
        QApplication.clipboard().setText(self.log_view.toPlainText())
        self.statusBar().showMessage("Log copiado para a área de transferência.", 3000)

    @Slot(str, str)
    def log(self, level, message):
        self.log_many(((level, message),))

    def log_many(self, entries):
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        lines = [f"{stamp} [{level}] {line}" for level, message in entries for line in message.splitlines()]
        if lines:
            self.log_view.appendPlainText('\n'.join(lines))

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Salvar log", "opldeck.log", "Log (*.log);;Texto (*.txt)")
        if path:
            try:
                with Path(path).open("x", encoding="utf-8") as stream:
                    stream.write(self.log_view.toPlainText() + "\n")
            except OSError as exc:
                self.show_error("Não foi possível salvar o log", str(exc))
            else:
                self.log("INFO", f"Log salvo em {path}")

    def show_error(self, title, message):
        self.log("ERROR", f"{title}: {message}")
        QMessageBox.warning(self, title, message)

    def show_details(self, table):
        row = table.currentRow()
        if not hasattr(self, "details"):
            return
        count = len(self._selected_rows(table))
        if count > 1:
            self.details.setPlainText(f"{count} jogos selecionados. Use o botão de conversão/exportação ou Ctrl+Enter "
                                      "para revisar o lote e escolher o destino.")
            return
        if row < 0 or not table.selectionModel().selectedRows() or table.isRowHidden(row):
            if table is self.active_table():
                self.details.clear()
            return
        values = [table.item(row, col).text() if table.item(row, col) else "—" for col in range(table.columnCount())]
        if table is self.bin_table:
            self.details.setPlainText(
                f"ID: {values[1]} | Título: {values[2]} | Setores: {values[5]}\n"
                f"Bytes raw: {values[7]} | Bytes ISO: {values[8]} | {values[9]}\n"
                f"Entrada: {values[6]}\nBIN: {values[10]}\nStatus: {values[0]} · EDC/ECC não verificados"
            )
            return
        self.details.setPlainText(
            f"ID: {values[1]} | Título: {values[2]} | Mídia: {values[3]}\n"
            f"Bytes: {values[7]} | CRC USBExtreme: {values[8]} | Partes UL: {values[5]}\n"
            f"Identificação: {values[9]} | Origem da mídia: {values[10]}\n"
            f"Caminho: {values[6]}\nStatus: {values[0]}"
        )

    def clear_table(self, table):
        if self.worker is not None:
            return
        with updating_table(table):
            table.setRowCount(0)
        self.details.clear()
        if table is self.iso_table:
            self.imported.clear()
            name = "ISO"
        elif table is self.bin_table:
            self.imported_bins.clear()
            name = "BIN/CUE"
        else:
            self.cfg_path = None
            name = "UL"
        self.log("INFO", f"Lista {name} limpa")
        self.statusBar().showMessage("Lista limpa")
        search, status_filter, _ = self.table_controls[table]
        with QSignalBlocker(search), QSignalBlocker(status_filter):
            search.clear()
            status_filter.setCurrentIndex(0)
        self.filter_table(table)

    def choose_isos(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Adicionar imagens de PS2", "", "Imagens ISO (*.iso *.ISO)")
        self.load_isos(paths)

    def choose_archives(self):
        if self.worker is not None:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, 'Importar jogos de ZIP/7z', '',
                                              'Todos os arquivos (*);;Compactados comuns (*.zip *.ZIP *.7z *.7Z)')
        if not paths:
            return
        self.archive_import.start(paths)

    def start_folder_archive(self, path, expected, preferences):
        self.archive_import.start([path], automatic=(expected, preferences))

    def update_import_search(self):
        monitor = getattr(self, 'import_monitor', None)
        enabled = bool(monitor and monitor.preferences.enabled and monitor.observed
                       and not monitor.closed and not monitor.worker and not self.worker
                       and not self.archive_import.active)
        self.import_search_button.setEnabled(enabled)
        self.import_search_button.setToolTip(
            'Buscar novamente os ZIP/7z pendentes da Pasta de import.' if enabled else
            'Disponível com a Pasta de import ativada, arquivos pendentes e nenhuma operação em andamento.')

    def start_managed_archives(self, plans, *, automatic=None):
        if self.worker is not None or not plans:
            return
        temporary = workspace_root().resolve()
        if any(temporary.is_relative_to(volume.root.resolve()) for volume in self.volumes):
            self.show_error('Pasta temporária inválida', 'A área temporária precisa estar no PC, fora dos pendrives.')
            return
        preferences = ImportPreferences.load(self.cover_preloader.settings)
        self.refresh_cfg = None
        if automatic:
            expected, preferences = automatic
            self._convert('Extração ZIP/7z', prepare_archive, (plans[0].source, expected, preferences), {'plan': plans[0]})
        else:
            self._convert('Extração ZIP/7z', prepare_imports, (tuple(plan.source for plan in plans), preferences), {'plans': plans})
        self.overall_progress.setFormat('Total (extração + transferência): %p%')
        self.overall_progress.setValue(0)
        self.overall_progress.show()
        self.cancel_button.setText('Cancelar extração')

    def import_cleanup_choice(self, records):
        if not records:
            return False
        policy = ImportPreferences.load(self.cover_preloader.settings).cleanup
        if policy == 'ask':
            return QMessageBox.question(self, 'Limpar arquivos extraídos',
                'Apagar os arquivos temporários depois que todos os jogos forem transferidos e verificados? '
                'Os arquivos ZIP/7z originais serão preservados.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
        return policy == 'always'

    def start_archives(self, paths, destination, *, automatic=None):
        if self.worker is not None or not paths:
            return
        self.refresh_cfg = None
        if automatic:
            expected, preferences = automatic
            self._convert('Extração ZIP', prepare_archive, (paths[0], expected, preferences), {})
        else:
            self._convert('Extração ZIP', extract_archives, (tuple(paths), destination), {})
        self.batch_index = 0
        self.batch_label.setText(f'{len(paths)} ZIP(s) · preparando extração das imagens ISO…')
        self.batch_label.show()
        self.overall_progress.setFormat('Total (extração + transferência): %p%')
        self.overall_progress.setValue(0)
        self.overall_progress.show()
        self.progress_bar.setFormat('Extração atual: %p%')
        self.cancel_button.setText('Cancelar extração')

    def extraction_credit(self, source):
        source = Path(source).resolve()
        previous = self.archive_credit.get(source)
        try:
            return previous[2] if previous and fingerprint(source) == previous else 0
        except OSError:
            return 0

    def prepare_combined_progress(self, credit, total):
        self.transfer_credit, self.transfer_total = credit, total
        if credit:
            self.overall_progress.setFormat('Total (extração + transferência): %p%')
            self.overall_progress.setValue(credit * 1000 // (credit + total))
            self.overall_progress.show()

    def archives_finished(self, result, *, automatic=False):
        self.timer.stop()
        self.worker = None
        self._busy(False)
        self.cancel_button.setEnabled(False)
        self.progress_bar.setValue(1000)
        self.overall_progress.setValue(500)
        self.batch_label.hide()
        for path in result.paths:
            try:
                self.archive_credit[path.resolve()] = fingerprint(path)
            except OSError as exc:
                self.log('WARNING', f'Arquivo extraído indisponível ao atualizar a lista: {path} · {exc}')
        message = (f'{len(result.paths)} ISO(s) extraídas. Revise a lista e inicie a transferência; '
                   f'o total azul continuará de onde parou. Arquivos em {result.directory}')
        if automatic:
            message += ' A limpeza segue a opção das configurações, após transferir e verificar todos os jogos do compactado.'
        else:
            message += ' Os arquivos serão mantidos após transferir.'
        self.activity.finish('Extração concluída · aguardando transferência', message, remember=True)
        self.log('INFO', message)
        for error in result.errors:
            self.log('WARNING', error)
        if result.errors:
            QMessageBox.warning(self, 'Importação concluída com avisos', '\n'.join(result.errors))
        self.statusBar().showMessage('Extração concluída. Selecione os jogos e inicie a transferência.')
        self.show_table(self.iso_table)
        self.load_isos([str(path) for path in result.paths])

    def load_isos(self, paths):
        if self.worker is not None:
            return
        new = sorted({str(Path(path).resolve()) for path in paths} - self.imported)
        if new:
            self.imported.update(new)
            self.log("INFO", f"Importação ISO: {len(new)} arquivo(s)")
            self._scan(iso_rows, new, self.iso_table)

    def choose_bins(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Adicionar BIN/CUE de PS2", "",
                                               "Imagens raw e CUE (*.bin *.BIN *.cue *.CUE)")
        self.load_bins(paths)

    def load_bins(self, paths):
        if self.worker is not None:
            return
        new = sorted({str(Path(path).resolve()) for path in paths} - self.imported_bins)
        if new:
            self.imported_bins.update(new)
            self.log("INFO", f"Importação BIN/CUE: {len(new)} arquivo(s); verificação inicial por metadados")
            self._scan(bin_rows, new, self.bin_table)

    def choose_cfg(self):
        path, _ = QFileDialog.getOpenFileName(self, "Abrir lista de jogos UL", "", "Lista UL (ul.cfg);;Todos os arquivos (*)")
        if path:
            self.load_cfg(path)

    def load_cfg(self, path):
        if self.worker is None:
            self.pending_cfg = Path(path)
            self.log("INFO", f"Abrindo índice: {path}")
            self._scan(ul_rows, str(path), self.ul_table)

    def _busy(self, busy):
        self.transfer_queue.set_busy(busy)
        self.cover_preloader.set_paused(busy)
        for button in self.buttons:
            button.setEnabled(not busy)
        if not busy:
            self.update_import_search()
        self._update_actions()
        if not busy:
            QTimer.singleShot(0, self.maybe_scan_device)
            QTimer.singleShot(0, self.refresh_storage)

    def _scan(self, function, argument, table, *, replace=False):
        self.target = table
        self.replace_scan = replace
        row = table.currentRow()
        self.selected_path = (table.item(row, 6).text() if row >= 0 and table.item(row, 6)
                              and table.selectionModel().selectedRows() else None)
        self.worker = ScanWorker(function, argument)
        self.worker.signals.finished.connect(self._finished)
        label = 'Jogos UL' if table is self.ul_table else 'Imagens ISO' if table is self.iso_table else 'Imagens BIN/CUE'
        self.activity.begin('Lendo arquivos…', f'{label} · identificando jogos e verificando os arquivos.', scan=True)
        self._busy(True)
        self.statusBar().showMessage("Lendo arquivos…")
        self.pool.start(self.worker)

    @Slot(object, str)
    def _finished(self, rows, error):
        if error:
            self.activity.finish_scan(error, error=True)
            self.statusBar().showMessage("Não foi possível abrir a lista")
            self.show_error("Falha na leitura", error)
        else:
            table = self.target
            with updating_table(table):
                if table is self.ul_table or self.replace_scan:
                    table.setRowCount(0)
                if table is self.ul_table:
                    self.cfg_path = self.pending_cfg
                self.append_rows(table, rows)
                if self.selected_path:
                    selected_row = next((row for row in range(table.rowCount())
                                         if table.item(row, 6).text() == self.selected_path), None)
                    if selected_row is not None:
                        table.selectRow(selected_row)
            self.filter_table(table)
            self.statusBar().showMessage(f"{table.rowCount()} arquivo(s) na lista · leitura concluída")
            self.log("INFO", f"Leitura concluída: {len(rows)} registro(s)")
            self.activity.finish_scan(f'Lista atualizada · {table.rowCount()} arquivo(s). Consulte os ícones da tabela para ver as falhas.')
        self.worker = None
        if not error:
            self.prepare_covers()
        self._busy(False)

    def append_rows(self, table, rows):
        entries = []
        with updating_table(table, pause_sorting=True):
            start = table.rowCount()
            table.setRowCount(start + len(rows))
            for row, values in enumerate(rows, start):
                severity, explanation = row_status(values, table is self.ul_table)
                for column, value in enumerate(values):
                    if column in (4, 5, 7, 8):
                        number = values[7] if column == 4 else value
                        base = 16 if column == 8 and table is not self.bin_table else 10
                        item = NumericTableItem(value, number, base=base)
                    else:
                        item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    if column == 0:
                        item.setData(STATUS_ROLE, severity)
                        item.setData(SEARCH_ROLE, searchable_values(values))
                        item.setIcon(self.status_icons[severity])
                        item.setToolTip(f"{STATUS_LABELS[severity]}\n{value}\n{explanation}")
                        item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,
                                     f"{STATUS_LABELS[severity]}. {explanation}")
                        item.setData(Qt.ItemDataRole.AccessibleTextRole, value)
                    table.setItem(row, column, item)
                metadata = (f"bytes_raw={values[7]} | bytes_ISO={values[8]} | setores={values[5]} | {values[9]}"
                            if table is self.bin_table else f"bytes={values[7]} | CRC={values[8]}")
                if severity == "warning" and explanation != values[0]:
                    metadata += " | " + explanation.replace("\n", " | ")
                entries.append(({"ok": "INFO", "warning": "WARNING", "error": "ERROR"}[severity],
                                f"{values[0]} | ID={values[1]} | {metadata} | {values[6]}"))
        self.log_many(entries)

    def _selected(self, table):
        rows = self._selected_rows(table)
        if len(rows) > 1:
            self.statusBar().showMessage("Selecione apenas um jogo para esta ação.")
            return None
        row = table.currentRow()
        if row < 0 or not rows or table.isRowHidden(row):
            self.statusBar().showMessage("Selecione um jogo na tabela.")
            return None
        return [table.item(row, col).text() for col in range(table.columnCount())]

    def _selected_values(self, table):
        return [[table.item(row, col).text() for col in range(table.columnCount())]
                for row in sorted(self._selected_rows(table))]

    def _selected_rows(self, table):
        return [index.row() for index in table.selectionModel().selectedRows()
                if not table.isRowHidden(index.row())]

    def choose_batch(self, table, kind):
        if self.worker is not None or kind == "export" and self.cfg_path is None:
            return
        values = self._selected_values(table)
        self._update_actions()
        if not self.table_actions[table]["convert"].isEnabled():
            self.statusBar().showMessage("Selecione jogos identificados e, para UL, com partes válidas.")
            return
        dialog = BatchDialog(values, kind, self.cfg_path, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.start_batch(dialog.jobs())

    def start_batch(self, jobs, *, volume=None, retry=False):
        if self.worker is not None or not jobs:
            return
        function = run_usb_batch if volume else run_batch
        args = (tuple(jobs), volume) if volume else (tuple(jobs),)
        records = self.import_monitor.for_jobs(jobs)
        cleanup = self.import_cleanup_choice(records)
        if self.worker is not None:
            return
        self.transfer_queue.begin(jobs, volume, retry=retry)
        self.tabs.setCurrentWidget(self.transfer_queue)
        if records:
            self._convert('Transferência em lote', transfer_with_import_cleanup,
                          (function, args, {}, records, tuple(jobs), volume, cleanup), {})
        else:
            self._convert('Transferência em lote', function, args, {})
        self.transfer_volume = volume
        self.batch_jobs = tuple(jobs)
        self.batch_index = 0
        self.batch_started = self.started
        self.batch_label.setText(f"Preparando {len(jobs)} jogos · verificando origens e destino…")
        self.batch_label.show()
        self.overall_progress.setValue(0)
        self.overall_progress.show()
        self.prepare_combined_progress(sum(self.extraction_credit(job.source) for job in jobs if job.kind == 'install'),
                                      sum(job.size for job in jobs))
        self.progress_bar.setFormat("Jogo atual: %p%")
        self.cancel_button.setText("Cancelar lote")

    def retry_batch(self):
        if self.worker is None and self.transfer_queue.pending_jobs:
            if self.transfer_queue.single_retry:
                callback, args = self.transfer_queue.single_retry
                callback(*args)
            else:
                self.start_batch(self.transfer_queue.pending_jobs, volume=self.transfer_queue.volume, retry=True)

    def choose_install(self):
        if len(self._selected_rows(self.iso_table)) > 1:
            self.choose_batch(self.iso_table, "install")
            return
        selected = self._selected(self.iso_table)
        if selected is None or self.worker is not None:
            return
        try:
            game = inspect_iso(selected[6])
        except (OSError, ValueError) as exc:
            self.show_error("ISO inválida", str(exc))
            return
        dialog = InstallDialog(game, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        directory = QFileDialog.getExistingDirectory(self, "Destino UL (raiz do dispositivo ou biblioteca)")
        if directory:
            self.start_install(game.path, directory, dialog.title_edit.text(), Media[dialog.media_combo.currentText()])

    def start_install(self, source, destination, title, media):
        if self.worker is None:
            self.refresh_cfg = Path(destination) / "ul.cfg"
            source, destination = Path(source), Path(destination)
            jobs = (TransferJob('install', source, destination, '', title, 0, media),)
            records = self.import_monitor.for_jobs(jobs)
            kwargs = {"title": title, "media": media}
            if records:
                cleanup = self.import_cleanup_choice(records)
                if self.worker is not None:
                    return
                self._convert('ISO → UL', transfer_with_import_cleanup,
                              (install_iso, (source, destination), kwargs, records, None, None,
                               cleanup), {})
            else:
                self._convert('ISO → UL', install_iso, (source, destination), kwargs)
            credit = self.extraction_credit(source)
            self.prepare_combined_progress(credit, credit)

    def choose_export(self):
        if len(self._selected_rows(self.ul_table)) > 1:
            self.choose_batch(self.ul_table, "export")
            return
        selected = self._selected(self.ul_table)
        if selected is None or self.cfg_path is None or self.worker is not None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exportar ISO (novo arquivo)", f"{selected[1]}.iso", "ISO (*.iso)")
        if path:
            self.start_export(self.cfg_path, selected[1], path)

    def start_export(self, cfg_path, game_id, destination):
        if self.worker is None:
            self.refresh_cfg = None
            self._convert("UL → ISO", export_ul, (cfg_path, game_id, destination), {})

    def choose_rename(self):
        selected = self._selected(self.ul_table)
        if selected is None or self.cfg_path is None or self.worker is not None:
            return
        if not self.table_actions[self.ul_table]['rename'].isEnabled():
            return
        title, accepted = QInputDialog.getText(
            self, "Renomear jogo UL", f"{selected[1]} · novo título (1–32 caracteres ASCII):\n"
            + ('As partes ausentes/defeituosas continuarão com falhas após renomear.'
               if row_status(selected, True)[0] != 'ok' else ''), text=selected[2])
        if accepted:
            self.start_rename(self.cfg_path, selected[1], title)

    def start_rename(self, cfg_path, game_id, title):
        if self.worker is None:
            self.refresh_cfg = Path(cfg_path)
            self._convert("Renomeação UL", rename_ul, (cfg_path, game_id, title), {})

    def choose_delete(self):
        selected = self._selected_values(self.ul_table)
        if not selected or self.cfg_path is None or self.worker is not None:
            return
        self._update_actions()
        if not self.table_actions[self.ul_table]['delete'].isEnabled():
            return
        try:
            plan = prepare_delete(self.cfg_path, (row[1] for row in selected))
        except (OSError, ValueError) as exc:
            self.show_error('Não foi possível preparar a exclusão', str(exc))
            return
        confirmation = QMessageBox(self)
        confirmation.setWindowTitle('Excluir jogo UL')
        confirmation.setIcon(QMessageBox.Icon.Question)
        confirmation.setTextFormat(Qt.TextFormat.PlainText)
        count = len(plan.games)
        present = sum(len(game.present) for game in plan.games)
        missing = sum(len(game.missing) for game in plan.games)
        total = sum(game.size for game in plan.games)
        entry = plan.games[0].entry
        confirmation.setText(f'Excluir permanentemente {entry.title} ({entry.game_id})?' if count == 1 else
                             f'Excluir permanentemente os {count} jogos selecionados?')
        confirmation.setInformativeText(
            f'{present} de {present + missing} parte(s) presentes serão removidas ({total:,} bytes).\n'
            f'{missing} parte(s) já ausentes. {count} registro(s) serão removidos de:\n{plan.cfg}\n'
            'Capas e arquivos de outros jogos serão mantidos.'
            + ('\nA exclusão é sequencial: falha ou cancelamento para os próximos jogos; os já excluídos permanecem excluídos.'
               if count > 1 else ''))
        confirmation.setDetailedText('\n\n'.join(
            f'{game.entry.title} ({game.entry.game_id})\nArquivos a remover:\n'
            + ('\n'.join(game.present) or '(nenhum — somente o registro)')
            + '\nPartes já ausentes:\n' + ('\n'.join(game.missing) or '(nenhuma)') for game in plan.games))
        confirmation.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        confirmation.setDefaultButton(QMessageBox.StandardButton.No)
        answer = confirmation.exec()
        if answer == QMessageBox.StandardButton.Yes:
            self.start_delete_selection(plan)

    def start_delete_selection(self, plan):
        if self.worker is not None:
            return
        self.refresh_cfg = plan.cfg
        self._convert('Exclusão UL', delete_selection, (plan,), {})
        self.batch_label.setText(f'Preparando exclusão de {len(plan.games)} jogo(s)…')
        self.batch_label.show()
        self.overall_progress.setValue(0)
        self.overall_progress.setFormat('Jogos excluídos: %p%')
        self.overall_progress.setVisible(len(plan.games) > 1)
        self.cancel_button.setText('Cancelar exclusão')

    def start_delete(self, cfg_path, game_id):
        if self.worker is None:
            self.refresh_cfg = Path(cfg_path)
            self._convert("Exclusão UL", delete_ul, (cfg_path, game_id), {})

    def choose_recovery(self):
        if self.worker is not None:
            return
        directory = QFileDialog.getExistingDirectory(
            self, "Biblioteca com operação UL pendente", str(self.cfg_path.parent) if self.cfg_path else "")
        if directory:
            self.refresh_cfg = Path(directory) / "ul.cfg"
            self._convert("Recuperação UL", recover_ul, (directory,), {})
            self.cancel_button.setEnabled(False)

    def choose_rebuild(self):
        if self.worker is not None:
            return
        directory = QFileDialog.getExistingDirectory(
            self, "Biblioteca com partes UL para reconstruir o índice",
            str(self.cfg_path.parent) if self.cfg_path else "")
        if directory:
            self.start_rebuild_scan(directory)

    def start_rebuild_scan(self, directory):
        if self.worker is not None:
            return
        self.worker = ScanWorker(scan_ul, directory)
        self.worker.signals.finished.connect(self._rebuild_scanned)
        self.activity.begin('Analisando partes UL…', 'Identificando jogos para a prévia de reconstrução.', scan=True)
        self._busy(True)
        self.statusBar().showMessage("Analisando partes UL e identificando jogos…")
        self.log("INFO", f"Análise para reconstrução de ul.cfg: {directory}")
        self.pool.start(self.worker)

    @Slot(object, str)
    def _rebuild_scanned(self, scan, error):
        self.worker = None
        self._busy(False)
        self.activity.finish_scan(error or 'Análise concluída. Confira os jogos encontrados na prévia.', error=bool(error))
        if error:
            self.show_error("Falha na análise de recuperação", error)
            self.statusBar().showMessage("Análise de recuperação interrompida")
            return
        if not scan.games:
            self.activity.finish_scan('Nenhum grupo de partes UL reconhecido.')
            self.show_error("Reconstrução de ul.cfg", "Nenhum grupo de partes UL reconhecido.\n" + "\n".join(scan.warnings))
            self.statusBar().showMessage("Nenhum jogo UL encontrado")
            return
        dialog = RebuildDialog(scan, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh_cfg = scan.directory / "ul.cfg"
            self._convert("Reconstrução UL", rebuild_ul, (scan, dialog.choices()),
                          {"replace_existing": scan.index_data is not None})
        else:
            self.statusBar().showMessage("Prévia fechada; nenhum arquivo foi alterado.")

    def choose_bin_conversion(self):
        if len(self._selected_rows(self.bin_table)) > 1:
            self.choose_batch(self.bin_table, "bin")
            return
        selected = self._selected(self.bin_table)
        if selected is None or self.worker is not None:
            return
        if selected[1] == "—":
            self.statusBar().showMessage("Selecione um BIN/CUE identificado como PS2.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Converter BIN/CUE para uma nova ISO",
                                             f"{selected[1]}.iso", "ISO (*.iso)")
        if path:
            self.start_bin_conversion(selected[6], path)

    def start_bin_conversion(self, source, destination):
        if self.worker is None:
            self.refresh_cfg = None
            self._convert("BIN → ISO", convert_bin, (source, destination), {})

    def _convert(self, operation, function, args, kwargs):
        display_function = args[0] if function is transfer_with_import_cleanup else function
        display_args = args[1] if function is transfer_with_import_cleanup else args
        display_kwargs = args[2] if function is transfer_with_import_cleanup else kwargs
        self.tracking_single = display_function in (install_iso, export_ul, convert_bin)
        if self.tracking_single:
            source, destination = Path(display_args[0]), Path(display_args[-1])
            if display_function is install_iso:
                kind, game_id, title = 'install', '', display_kwargs.get('title') or source.stem
                retry = (self.start_install, (source, destination, title, display_kwargs.get('media')))
            elif display_function is export_ul:
                kind, game_id, title = 'export', display_args[1], display_args[1]
                retry = (self.start_export, (source, game_id, destination))
            else:
                kind, game_id, title = 'bin', '', source.stem
                retry = (self.start_bin_conversion, (source, destination))
            table = self.iso_table if kind == 'install' else self.bin_table if kind == 'bin' else self.ul_table
            for row in range(table.rowCount()):
                matches = (table.item(row, 1).text() == game_id if kind == 'export' else
                           Path(table.item(row, 6).text()).absolute() == source.absolute())
                if matches:
                    game_id = table.item(row, 1).text()
                    if kind != 'install':
                        title = table.item(row, 2).text()
                    break
            self.transfer_queue.begin((TransferJob(kind, source, destination, game_id, title, 0),))
            self.transfer_queue.single_retry = retry
            self.tabs.setCurrentWidget(self.transfer_queue)
        self.transfer_credit = self.transfer_total = 0
        self.progress_bar.set_extraction(operation.startswith('Extração'))
        self.activity.begin(f'{operation} em andamento…', 'Preparando operação · verificando origem e destino.')
        self.operation_progress.show()
        self.progress_metrics.clear()
        self.progress_metrics.hide()
        self.overall_progress.setFormat('Total do lote: %p%')
        self.batch_jobs = ()
        self.batch_label.hide()
        if operation in ('ISO → UL', 'UL → ISO', 'BIN → ISO', 'Renomeação UL'):
            game = str(display_args[1]) if operation in ('UL → ISO', 'Renomeação UL') else Path(display_args[0]).name
            self.batch_label.setText(f'Jogo atual · {game}')
            self.batch_label.show()
        self.overall_progress.hide()
        self.progress_bar.setFormat("%p%")
        self.cancel_button.setText("Cancelar operação")
        self.operation = operation
        self.started = time.monotonic()
        self.transfer_rate.reset()
        self.transfer_rate.observe(self.started, 0)
        self.speed_drop_reported = False
        self.last_progress = None
        self.last_log_percent = -10
        self.progress_bar.setValue(0)
        self.progress_bar.setRange(0, 0)
        self.worker = ConversionWorker(function, args, kwargs)
        self.worker.signals.progress.connect(self._progress)
        self.worker.signals.finished.connect(self._converted)
        self._busy(True)
        self.cancel_button.setEnabled(True)
        parameters = (f"{len(display_args[0])} jogos | destino={display_args[0][0].output_directory}" if display_function in (run_batch, run_usb_batch) else
                      f"{args[0].directory} | grupos={len(args[0].games)}" if operation == "Reconstrução UL"
                      else " | ".join(str(arg) for arg in display_args))
        self.log("INFO", f"Iniciando {operation} | {parameters}")
        self.statusBar().showMessage(f"{operation} · verificando origem e destino…")
        self.timer.start()
        self.pool.start(self.worker)

    @Slot(object)
    def _progress(self, event: Progress):
        if isinstance(event, str):
            self.activity.message.setText(event)
            self.statusBar().showMessage(event)
            self.log('INFO', event)
            return
        if isinstance(event, DeleteProgress):
            self.batch_label.setText(f'Excluindo jogo {event.index} de {event.count} · {event.title}')
            self.overall_progress.setValue((event.index - 1) * 1000 // event.count)
            self._progress(event.current)
            return
        if isinstance(event, BatchProgress):
            self._batch_progress(event)
            return
        self.last_progress = event
        if self.tracking_single and self.transfer_queue.running:
            job = self.transfer_queue.jobs[0]
            self.transfer_queue.progress(BatchProgress(1, 1, job.title, self.operation, event,
                                                        event.completed, event.total, event.phase))
        self.transfer_rate.observe(time.monotonic(), event.completed)
        self.progress_bar.setRange(0, 1000)
        # Gerenciar um registro sem partes (ou somente partes vazias) envolve zero bytes.
        completed, total = (event.completed, event.total) if event.total else (event.part, max(event.parts, 1))
        percent = completed * 100 // total
        self.progress_bar.setValue(min(999, completed * 1000 // total))
        if self.transfer_credit and not self.batch_jobs:
            self.overall_progress.setValue(min(999, (self.transfer_credit + event.completed) * 1000 //
                                               (self.transfer_credit + event.total)))
        self.activity.phase('Sincronizando…' if event.phase == 'sync' else
                            'Finalizando operação · aguarde a confirmação.' if completed >= total
                            else f'{self.operation} · {percent}% concluído')
        if percent >= self.last_log_percent + 10:
            unit = self.transfer_unit(event)
            measure = "registros" if self.operation == "Reconstrução UL" else "bytes"
            self.log("INFO", f"{self.operation} | {percent}% | {measure}={event.completed}/{event.total} | {unit}")
            self.last_log_percent = percent
        self.update_transfer()

    def _batch_progress(self, event):
        self.transfer_queue.progress(event)
        if event.index != self.batch_index:
            self.batch_index = event.index
            self.started = time.monotonic()
            self.last_log_percent = -10
            self.transfer_rate.reset()
            self.transfer_rate.observe(self.started, 0)
            self.speed_drop_reported = False
        self.operation = event.operation
        self.progress_bar.set_extraction(event.operation.startswith(('Extração', 'Preparação BIN/CUE')))
        self._progress(event.current)
        if event.phase != "complete":
            self.progress_bar.setValue(min(self.progress_bar.value(), 999))
        total_value = (self.transfer_credit + event.overall_completed) * 1000 // (self.transfer_credit + event.overall_total)
        self.overall_progress.setValue(total_value if event.phase == "complete" and event.index == event.count
                                       else min(total_value, 999))
        phase = {"preparing": "preparando", "transfer": "transferindo", "complete": "concluído", 'sync': 'Sincronizando…'}[event.phase]
        if event.operation.startswith('Extração') and event.phase == 'transfer':
            phase = 'extraindo'
        self.batch_label.setText(f"Jogo {event.index} de {event.count} · {event.title} · {phase}")
        self.activity.phase(f'{event.index} de {event.count} jogos · {phase.capitalize()}'
                            + (' · finalizando gravação' if event.phase == 'transfer' and event.current.completed == event.current.total else ''))

    def transfer_unit(self, event):
        if self.operation == "Reconstrução UL":
            return f"registro {event.completed}/{event.total}"
        if self.operation == "BIN → ISO":
            return f"setores {event.completed // 2048}/{event.total // 2048} · bytes ISO"
        return f"parte {event.part}/{event.parts}"

    def update_transfer(self):
        elapsed = max(time.monotonic() - self.started, 0.001)
        event = self.last_progress
        if event is None:
            self.transfer_details.setText(f"{self.operation} · preparando · decorrido {duration_text(elapsed)}")
            return
        if self.operation == "Reconstrução UL":
            self.transfer_details.setText(f"Reconstrução UL · {self.transfer_unit(event)} · decorrido {duration_text(elapsed)}")
            return
        if self.operation in ("Renomeação UL", "Exclusão UL"):
            self.transfer_details.setText(
                f"{self.operation} · {self.transfer_unit(event)} · {event.completed:,} / {event.total:,} bytes referenciados · "
                f"decorrido {duration_text(elapsed)} · atualização de nomes e índice")
            return
        if event.phase == 'sync':
            self.progress_metrics.setText('Dados copiados · Sincronizando… Aguarde a confirmação antes de remover o pendrive.')
            self.progress_metrics.show()
            return
        rate, eta, dropped = self.transfer_rate.estimate(time.monotonic(), event.completed, event.total)
        if dropped and not self.speed_drop_reported:
            self.log('WARNING', 'Queda de velocidade detectada nos últimos 10 segundos; estimativa recalculada.')
        self.speed_drop_reported = dropped
        if self.activity.timer.isActive():
            self.progress_metrics.setText(
                f'{size_text(event.completed)} de {size_text(event.total)} · {rate / (1 << 20):.1f} MiB/s'
                + (f' · cerca de {duration_text(eta)} restantes' if eta is not None and event.completed < event.total else '')
                + (' · Queda de velocidade detectada' if dropped else ''))
            self.progress_metrics.show()
        self.transfer_details.setText(
            f"{self.operation} · {event.completed:,} / {event.total:,} bytes · "
            f"{self.transfer_unit(event)} · últimos 30 s: {rate / (1 << 20):.2f} MiB/s · "
            f"decorrido {duration_text(elapsed)} · restante estimado {duration_text(eta) if eta is not None else 'aguardando velocidade'} · buffer {BUFFER_SIZE:,} bytes"
        )

    def cancel_conversion(self):
        if isinstance(self.worker, ConversionWorker):
            self.worker.cancel.set()
            self.transfer_queue.cancel.setEnabled(False)
            self.activity.cancel_pending()
            self.cancel_button.setEnabled(False)
            self.log("WARNING", "Cancelamento solicitado; aguardando limpeza dos parciais.")
            self.statusBar().showMessage("Cancelando operação…")

    @Slot(object, str)
    def _converted(self, result, error):
        self.transfer_volume = None
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(0)
        cleanup_warnings = ()
        if isinstance(result, FolderTransferResult):
            removed, cleanup_warnings = result.removed, result.warnings
            result = result.transfer
            removed_paths = set(removed)
            with updating_table(self.iso_table):
                for row in range(self.iso_table.rowCount() - 1, -1, -1):
                    if self.iso_table.item(row, 6).text() in removed_paths:
                        self.iso_table.removeRow(row)
            for name in removed:
                self.imported.discard(name)
                self.archive_credit.pop(Path(name), None)
            if removed:
                self.filter_table(self.iso_table)
                self.log('INFO', f'Pasta de import: {len(removed)} ISO(s) temporárias removidas após verificação SHA-256.')
            for warning in cleanup_warnings:
                self.log('WARNING', warning)
            if cleanup_warnings:
                QMessageBox.warning(self, 'Jogos transferidos; limpeza pendente', '\n'.join(cleanup_warnings))
        if isinstance(result, FolderImportResult):
            self.import_monitor.register(result.record)
            self.archives_finished(result.archive_result, automatic=True)
            return
        if isinstance(result, FolderImportBatch):
            for record in result.records:
                self.import_monitor.register(record)
            self.archives_finished(result.archive_result, automatic=True)
            return
        if isinstance(result, ArchiveResult):
            self.archives_finished(result)
            return
        if isinstance(result, DeleteResult):
            self._deleted_selection(result)
            return
        if isinstance(result, BatchResult):
            self._batch_finished(result)
            return
        if self.tracking_single and self.transfer_queue.running:
            job = self.transfer_queue.jobs[0]
            if isinstance(result, ULEntry):
                self.transfer_queue.table.item(0, 2).setText(result.game_id)
            self.transfer_queue.finish(BatchResult(self.transfer_queue.jobs, () if error else (result,), 1,
                                                  error, error.startswith('Conversão cancelada')))
            if not error:
                game_id = result.game_id if isinstance(result, ULEntry) else job.game_id
                title = result.title if isinstance(result, ULEntry) else job.title
                dest = str(self.refresh_cfg or (job.destination / "ul.cfg")) if isinstance(result, ULEntry) else str(result)
                self._record_transfer_history([(game_id, title, OPERATIONS.get(job.kind, self.operation), job.source, dest)])
        if self.batch_jobs and self.transfer_queue.running and error:
            self.transfer_queue.finish(BatchResult(self.batch_jobs, (), 0, error,
                                                  error.startswith('Conversão cancelada')))
        self.timer.stop()
        self.update_transfer()
        self.worker = None
        self._busy(False)
        self.cancel_button.setEnabled(False)
        elapsed = time.monotonic() - self.started
        if error:
            if self.operation.startswith('Extração'):
                self.overall_progress.setValue(0)
                self.progress_bar.setValue(0)
            elif self.transfer_credit:
                self.overall_progress.setValue(self.transfer_credit * 1000 // (self.transfer_credit + self.transfer_total))
            cancelled = error.startswith('Conversão cancelada')
            self.activity.finish(f'{self.operation} cancelada' if cancelled else f'{self.operation} interrompida',
                                 error.splitlines()[0], 'warning' if cancelled else 'error', remember=True)
            self.log("WARNING" if error.startswith("Conversão cancelada") else "ERROR", error)
            self.statusBar().showMessage(error.splitlines()[0])
            self.transfer_details.setText(f"{self.operation} · interrompida após {duration_text(elapsed)} · {error.splitlines()[0]}")
            if not error.startswith("Conversão cancelada"):
                QMessageBox.warning(self, "Falha na operação", error)
            if self.operation in ("Renomeação UL", "Exclusão UL", "Recuperação UL", "Reconstrução UL") and self.refresh_cfg.exists():
                self.load_cfg(self.refresh_cfg)
        else:
            self.progress_bar.setValue(1000)
            if self.transfer_credit:
                self.overall_progress.setValue(1000)
            self.activity.finish(f'{self.operation} concluída', f'Operação finalizada em {duration_text(elapsed)}.', remember=True)
            detail = f"ID={result.game_id} | partes={result.parts} | CRC={result.crc:08X}" if isinstance(result, ULEntry) else str(result)
            self.log("INFO", f"{self.operation} concluída em {duration_text(elapsed)} | {detail}")
            self.statusBar().showMessage(f"{self.operation} concluída em {duration_text(elapsed)}")
            if self.refresh_cfg and self.refresh_cfg.exists():
                self.show_table(self.ul_table)
                self.load_cfg(self.refresh_cfg)
            elif self.operation == "BIN → ISO":
                self.show_table(self.iso_table)
                self.load_isos([str(result)])
            if self.operation == "Recuperação UL":
                self.transfer_details.setText(str(result))

    def _deleted_selection(self, result):
        self.timer.stop()
        self.update_transfer()
        self.worker = None
        self._busy(False)
        self.cancel_button.setEnabled(False)
        count = len(result.removed)
        total = len(result.plan.games)
        state = 'cancelada' if result.cancelled else 'interrompida' if result.error else 'concluída'
        summary = f'Exclusão UL {state} · {count} de {total} jogos excluídos · {total - count} não excluídos'
        self.activity.finish(f'Exclusão UL {state}', f'{count} de {total} jogos excluídos · {total - count} não excluídos',
                             'warning' if result.cancelled else 'error' if result.error else 'ok', remember=True)
        self.batch_label.setText(summary)
        self.batch_label.hide()
        self.transfer_details.setText(self.transfer_details.text() + '\n' + summary)
        self.overall_progress.setValue(count * 1000 // total)
        if not result.error:
            self.progress_bar.setValue(1000)
        self.statusBar().showMessage(summary)
        self.log('WARNING' if result.cancelled else 'ERROR' if result.error else 'INFO', summary)
        for game in result.plan.games:
            if game.entry.game_id in result.removed:
                self.log('INFO', f'Jogo excluído | ID={game.entry.game_id} | {game.entry.title}')
        if result.error:
            self.log('WARNING' if result.cancelled else 'ERROR', result.error)
            if not result.cancelled:
                QMessageBox.warning(self, 'Exclusão interrompida', summary + '\n\n' + result.error)
        if result.plan.cfg.exists():
            self.load_cfg(result.plan.cfg)

    def _batch_finished(self, result):
        self.transfer_queue.finish(result)
        if result.completed:
            entries = []
            for job, item in zip(result.jobs, result.completed):
                game_id = item.game_id if isinstance(item, ULEntry) else job.game_id
                title = item.title if isinstance(item, ULEntry) else job.title
                dest = str(job.destination / "ul.cfg") if isinstance(item, ULEntry) else str(item)
                entries.append((game_id, title, OPERATIONS.get(job.kind, self.operation), job.source, dest))
            self._record_transfer_history(entries)
        self.timer.stop()
        self.worker = None
        self._busy(False)
        self.cancel_button.setEnabled(False)
        elapsed = time.monotonic() - self.batch_started
        count = len(result.completed)
        state = "cancelado" if result.cancelled else "interrompido" if result.error else "concluído"
        summary = f"Lote {state} · {count} de {len(result.jobs)} jogos concluídos · {result.pending} não iniciados"
        if result.attempted > count:
            summary += f" · {result.attempted - count} interrompido"
        self.activity.finish(f'Lote {state}', summary + f' · {duration_text(elapsed)}',
                             'warning' if result.cancelled else 'error' if result.error else 'ok', remember=True)
        self.batch_label.setText(summary)
        self.batch_label.hide()
        self.transfer_details.setText(f"{summary} · decorrido {duration_text(elapsed)}")
        total = sum(job.size for job in result.jobs)
        self.overall_progress.setValue((self.transfer_credit + sum(job.size for job in result.jobs[:count])) * 1000 //
                                       (self.transfer_credit + total))
        if not result.error:
            self.progress_bar.setValue(1000)
        elif result.attempted == 0:
            self.progress_bar.setValue(0)
        self.statusBar().showMessage(summary)
        self.log("WARNING" if result.cancelled else "ERROR" if result.error else "INFO", summary)
        if result.error:
            self.log("WARNING" if result.cancelled else "ERROR", result.error)
            if not result.cancelled:
                QMessageBox.warning(self, "Transferência em lote interrompida", summary + "\n\n" + result.error)
        if result.jobs[0].kind == "install":
            cfg = result.jobs[0].destination / "ul.cfg"
            if cfg.exists():
                self.show_table(self.ul_table)
                self.load_cfg(cfg)
        elif result.completed:
            self.show_table(self.iso_table)
            self.load_isos([str(path) for path in result.completed])

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "log_highlighter"):
            self.log_highlighter.rehighlight()

    def closeEvent(self, event):
        if self.worker is not None:
            self.statusBar().showMessage("Cancele a conversão ou aguarde a operação terminar antes de fechar.")
            event.ignore()
        else:
            self.import_monitor.close()
            self.cover_preloader.close()
            self.device_monitor.stop()
            self.device_scan_timer.stop()
            self.storage_timer.stop()
            self.device_animation.stop()
            self.activity.timer.stop()
            self.pending_volume = False
            self.app_logger.removeHandler(self.log_handler)
            self.log_handler.close()
            super().closeEvent(event)


def run() -> int:
    app = QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    theme_manager()
    window = MainWindow()
    window.show()
    return app.exec()
