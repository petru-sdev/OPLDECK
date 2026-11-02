"""Paletas e preferências de aparência. Não faz parte do núcleo de conversões."""

from dataclasses import dataclass
import re

from PySide6.QtCore import QEvent, QObject, QSettings, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

from opldeck.ui.preferences import application_settings, save_preferences


# Os significados dos diagnósticos permanecem iguais em todos os temas.
STATUS_COLORS = {
    "ok": ("#166534", "#86efac", "#15803d"),
    "warning": ("#854d0e", "#fde047", "#a16207"),
    "error": ("#991b1b", "#fca5a5", "#b91c1c"),
}


def status_color(severity, palette):
    light, dark, _ = STATUS_COLORS[severity]
    return QColor(dark if palette.color(QPalette.ColorRole.Base).lightness() < 128 else light)


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    description: str
    colors: dict[str, str] | None = None


THEMES = {
    "system": Theme("system", "Sistema", "Mantém a aparência nativa do Qt e do ambiente de trabalho."),
    "light": Theme("light", "Claro", "Superfícies claras e cores discretas, com os diagnósticos preservados.", {
        "Window": "#f0f1f3", "WindowText": "#20252b", "Base": "#ffffff", "AlternateBase": "#f4f5f7",
        "Text": "#20252b", "Button": "#e7e9ec", "ButtonText": "#20252b",
        "ToolTipBase": "#ffffef", "ToolTipText": "#20252b", "BrightText": "#ffffff",
        "Highlight": "#295d8c", "HighlightedText": "#ffffff", "Link": "#245b8d", "LinkVisited": "#6a468c",
        "PlaceholderText": "#646b75", "Light": "#ffffff", "Midlight": "#f4f5f7", "Mid": "#bbc1c9",
        "Dark": "#929aa5", "Shadow": "#555d68", "disabled_text": "#747b85", "disabled_highlight": "#cbd1d9",
    }),
    "dark": Theme("dark", "Escuro", "Superfícies escuras e cores discretas, com os diagnósticos preservados.", {
        "Window": "#24272b", "WindowText": "#e5e7eb", "Base": "#1b1e22", "AlternateBase": "#22262b",
        "Text": "#e5e7eb", "Button": "#30343a", "ButtonText": "#e5e7eb",
        "ToolTipBase": "#30343a", "ToolTipText": "#e5e7eb", "BrightText": "#ffffff",
        "Highlight": "#3c668d", "HighlightedText": "#ffffff", "Link": "#8bbce8", "LinkVisited": "#c0a4e0",
        "PlaceholderText": "#a0a8b4", "Light": "#4b525c", "Midlight": "#3c424b", "Mid": "#333941",
        "Dark": "#171a1e", "Shadow": "#101215", "disabled_text": "#8b939f", "disabled_highlight": "#343c47",
    }),
}

# As variantes reutilizam os papéis neutros já definidos e substituem sua
# identidade visual. Os diagnósticos continuam em STATUS_COLORS.
THEMES.update({
    "midnight": Theme("midnight", "Meia-noite", "Azul profundo com destaques em azul-claro, para ambientes escuros.", {
        **THEMES["dark"].colors,
        "Window": "#141d2f", "WindowText": "#e4edfc", "Base": "#0c1424", "AlternateBase": "#162238",
        "Text": "#e4edfc", "Button": "#22324b", "ButtonText": "#e4edfc",
        "ToolTipBase": "#22324b", "ToolTipText": "#e4edfc",
        "Highlight": "#8bbcff", "HighlightedText": "#091527", "Link": "#8bbcff", "LinkVisited": "#c3b3ff",
        "PlaceholderText": "#a0b2cc", "Light": "#425779", "Midlight": "#2d4160", "Mid": "#293b56",
        "Dark": "#0a1120", "Shadow": "#050a13", "disabled_text": "#8b9bb4", "disabled_highlight": "#26364f",
    }),
    "forest": Theme("forest", "Floresta", "Verde escuro com destaques em menta e superfícies suaves.", {
        **THEMES["dark"].colors,
        "Window": "#18251f", "WindowText": "#e5f1e9", "Base": "#101b16", "AlternateBase": "#1d2d24",
        "Text": "#e5f1e9", "Button": "#2b3d32", "ButtonText": "#e5f1e9",
        "ToolTipBase": "#2b3d32", "ToolTipText": "#e5f1e9",
        "Highlight": "#8ed6b0", "HighlightedText": "#10251a", "Link": "#8ed6b0", "LinkVisited": "#c5c1ee",
        "PlaceholderText": "#a6bcae", "Light": "#4d6757", "Midlight": "#37503f", "Mid": "#304637",
        "Dark": "#0b140f", "Shadow": "#060d08", "disabled_text": "#91a597", "disabled_highlight": "#304538",
    }),
    "amethyst": Theme("amethyst", "Ametista", "Violeta escuro com destaques em lavanda e texto claro.", {
        **THEMES["dark"].colors,
        "Window": "#261d30", "WindowText": "#f0e8f7", "Base": "#1b1323", "AlternateBase": "#2a2036",
        "Text": "#f0e8f7", "Button": "#3b2e49", "ButtonText": "#f0e8f7",
        "ToolTipBase": "#3b2e49", "ToolTipText": "#f0e8f7",
        "Highlight": "#c8a6f5", "HighlightedText": "#251335", "Link": "#d3b5ff", "LinkVisited": "#efb6d7",
        "PlaceholderText": "#baa9ca", "Light": "#655077", "Midlight": "#4e3c60", "Mid": "#44344f",
        "Dark": "#140d1b", "Shadow": "#0b0610", "disabled_text": "#a592b6", "disabled_highlight": "#43314f",
    }),
    "sand": Theme("sand", "Areia", "Fundo claro e quente, com detalhes em terracota e texto escuro.", {
        **THEMES["light"].colors,
        "Window": "#f1e9db", "WindowText": "#392c23", "Base": "#fffaf0", "AlternateBase": "#f5eddf",
        "Text": "#392c23", "Button": "#e6dac7", "ButtonText": "#392c23",
        "ToolTipBase": "#fff4d7", "ToolTipText": "#392c23",
        "Highlight": "#865237", "HighlightedText": "#ffffff", "Link": "#794528", "LinkVisited": "#765071",
        "PlaceholderText": "#756454", "Light": "#fffdf6", "Midlight": "#f9f1e4", "Mid": "#c6b79e",
        "Dark": "#a49379", "Shadow": "#706049", "disabled_text": "#817462", "disabled_highlight": "#d5c7b1",
    }),
    "high_contrast": Theme("high_contrast", "Alto contraste", "Fundo preto, texto branco e seleção amarela para maior contraste.", {
        **THEMES["dark"].colors,
        "Window": "#080808", "WindowText": "#ffffff", "Base": "#000000", "AlternateBase": "#181818",
        "Text": "#ffffff", "Button": "#202020", "ButtonText": "#ffffff",
        "ToolTipBase": "#ffffff", "ToolTipText": "#000000",
        "Highlight": "#ffe066", "HighlightedText": "#000000", "Link": "#8cdcff", "LinkVisited": "#e2b5ff",
        "PlaceholderText": "#d0d0d0", "Light": "#aaaaaa", "Midlight": "#727272", "Mid": "#636363",
        "Dark": "#505050", "Shadow": "#000000", "disabled_text": "#a8a8a8", "disabled_highlight": "#303030",
    }),
})


DENSITIES = {'normal': ('Padrão', 34), 'compact': ('Compacta', 26), 'comfortable': ('Espaçosa', 44)}


@dataclass(frozen=True)
class Personalization:
    accent: str = ''
    font_scale: int = 100
    density: str = 'normal'

    def __post_init__(self):
        if not isinstance(self.accent, str) or (self.accent and not re.fullmatch(r'#[0-9a-fA-F]{6}', self.accent)):
            raise ValueError('Cor de destaque inválida. Use uma cor RGB sem transparência.')
        if type(self.font_scale) is not int or not 80 <= self.font_scale <= 150:
            raise ValueError('O tamanho do texto deve ficar entre 80% e 150%.')
        if not isinstance(self.density, str) or self.density not in DENSITIES:
            raise ValueError('Espaçamento das listas inválido.')

    def values(self):
        return {'appearance/accent': self.accent, 'appearance/font_scale': self.font_scale,
                'appearance/density': self.density}

    @classmethod
    def load(cls, settings):
        scale = settings.value('appearance/font_scale', 100)
        if isinstance(scale, str) and scale.isdecimal():
            scale = int(scale)
        return cls(settings.value('appearance/accent', ''), scale, settings.value('appearance/density', 'normal'))


def accent_text(color):
    """Escolhe preto/branco pelo maior contraste com a seleção personalizada."""
    components = [value / 255 for value in color.getRgb()[:3]]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in components]
    light = sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    return QColor('#000000' if (light + .05) / .05 >= 1.05 / (light + .05) else '#ffffff')


def theme_palette(theme: Theme) -> QPalette:
    """Paleta explícita dos temas do aplicativo; Sistema usa a paleta nativa."""
    if theme.colors is None:
        raise ValueError("O tema Sistema usa a paleta nativa, não uma paleta fixa.")
    palette = QPalette()
    for name, color in theme.colors.items():
        if not name.startswith("disabled_"):
            palette.setColor(getattr(QPalette.ColorRole, name), QColor(color))
    palette.setColor(QPalette.ColorRole.Accent, QColor(theme.colors["Highlight"]))
    disabled = QPalette.ColorGroup.Disabled
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.PlaceholderText, QPalette.ColorRole.HighlightedText):
        palette.setColor(disabled, role, QColor(theme.colors["disabled_text"]))
    palette.setColor(disabled, QPalette.ColorRole.Highlight, QColor(theme.colors["disabled_highlight"]))
    return palette


def palette_snapshot(palette):
    # Roles não resolvidos (como Accent em alguns estilos nativos) poderiam
    # herdar uma cor do tema anterior ao voltar ao Sistema. Fixamos a cópia.
    snapshot = QPalette(palette)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        for role in QPalette.ColorRole:
            if role not in (QPalette.ColorRole.NoRole, QPalette.ColorRole.NColorRoles):
                snapshot.setBrush(group, role, palette.brush(group, role))
    return snapshot


class ThemeManager(QObject):
    changed = Signal(str)
    SETTINGS_KEY = "appearance/theme"

    def __init__(self, app, settings=None):
        super().__init__(app)
        self.app = app
        self.settings = settings if settings is not None else application_settings()
        self.native_style = app.style().objectName()
        self.native_palette = palette_snapshot(app.palette())
        self.native_font = QFont(app.font())
        self.current = "system"
        self.personalization = Personalization()
        self.warning = ""
        self._applying = False
        app.installEventFilter(self)

    def eventFilter(self, watched, event):
        # Atualizações nativas recebidas enquanto Sistema está ativo são preservadas.
        if (watched is self.app and event.type() == QEvent.Type.ApplicationPaletteChange
                and self.current == "system" and not self.personalization.accent and not self._applying):
            self.native_palette = palette_snapshot(self.app.palette())
        if (watched is self.app and event.type() == QEvent.Type.ApplicationFontChange
                and self.personalization.font_scale == 100 and not self._applying):
            self.native_font = QFont(self.app.font())
        return super().eventFilter(watched, event)

    def restore(self):
        self.warning = ""
        key = self.settings.value(self.SETTINGS_KEY, "system")
        if not isinstance(key, str) or key not in THEMES:
            self.warning = "O tema salvo não está disponível. O programa iniciou com a aparência do sistema."
            key = "system"
        elif self.settings.status() != QSettings.Status.NoError:
            self.warning = "Não foi possível ler as preferências. O programa iniciou com a aparência do sistema."
            key = "system"
        try:
            personalization = Personalization.load(self.settings)
        except ValueError:
            personalization = Personalization()
            self.warning = 'A personalização salva é inválida. Cores, texto e espaçamento voltaram ao padrão.'
        # Primeira abertura sem preferência não altera estilo nem paleta.
        if key != "system" or self.current != "system" or personalization != self.personalization:
            self.apply(key, personalization)

    def apply(self, key, personalization=None):
        if key not in THEMES:
            raise ValueError("Tema desconhecido.")
        personalization = self.personalization if personalization is None else personalization
        if not isinstance(personalization, Personalization):
            raise ValueError('Personalização inválida.')
        palette = palette_snapshot(self.native_palette) if key == 'system' else theme_palette(THEMES[key])
        if personalization.accent:
            accent = QColor(personalization.accent)
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                palette.setColor(group, QPalette.ColorRole.Highlight, accent)
                palette.setColor(group, QPalette.ColorRole.Accent, accent)
                palette.setColor(group, QPalette.ColorRole.HighlightedText, accent_text(accent))
        font = QFont(self.native_font)
        if personalization.font_scale != 100:
            if font.pointSizeF() > 0:
                font.setPointSizeF(font.pointSizeF() * personalization.font_scale / 100)
            else:
                font.setPixelSize(max(1, round(font.pixelSize() * personalization.font_scale / 100)))
        style_name = self.native_style if key == "system" else "Fusion"
        self._applying = True
        try:
            if self.app.style().objectName().casefold() != style_name.casefold():
                style = QStyleFactory.create(style_name)
                if style is None:
                    raise ValueError(f"O estilo Qt {style_name} não está disponível.")
                self.app.setStyle(style)
            self.app.setPalette(palette)
            self.app.setFont(font)
            self.current = key
            self.personalization = personalization
        finally:
            self._applying = False
        self.changed.emit(key)

    def save(self, extra_values=None):
        save_preferences(self.settings, {**(extra_values or {}), self.SETTINGS_KEY: self.current,
                                         **self.personalization.values()})
        self.warning = ""

    def row_height(self, metrics):
        height = DENSITIES[self.personalization.density][1] * self.personalization.font_scale / 100
        return max(round(height), metrics.height() + 8)


def theme_manager():
    """Um gerenciador por QApplication, compartilhado entre janela e diálogos."""
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("A interface Qt precisa estar iniciada para escolher um tema.")
    manager = getattr(app, "_opldeck_themes", None)
    if manager is None:
        manager = ThemeManager(app)
        app._opldeck_themes = manager
        manager.restore()
    return manager
