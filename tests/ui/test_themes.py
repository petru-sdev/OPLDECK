import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication, QDialog, QStyle, QStyleOptionViewItem
    from opldeck.ui.gui import MainWindow, STATUS_ROLE
    from opldeck.ui.game_gui import GameDialog
    from opldeck.ui.settings_gui import SettingsDialog
    from opldeck.ui.themes import THEMES, ThemeManager, palette_snapshot, status_color, theme_palette
except ModuleNotFoundError:
    QApplication = None

from tests.fixtures import make_iso


def luminance(color):
    rgb = [v / 255 for v in color.getRgb()[:3]]
    rgb = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return sum(v * weight for v, weight in zip(rgb, (0.2126, 0.7152, 0.0722)))


def contrast(a, b):
    low, high = sorted((luminance(a), luminance(b)))
    return (high + 0.05) / (low + 0.05)


@unittest.skipIf(QApplication is None, "PySide6 não instalado")
class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.native_style = self.app.style().objectName()
        self.native_palette = palette_snapshot(self.app.palette())
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "settings.ini"
        self.settings = QSettings(str(self.path), QSettings.Format.IniFormat)
        self.manager = ThemeManager(self.app, self.settings)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app.removeEventFilter(self.manager)
        self.manager.deleteLater()
        self.app.setStyle(self.native_style)
        self.app.setPalette(self.native_palette)
        self.app.processEvents()
        self.temp.cleanup()

    def assert_palette_equal(self, left, right):
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
            for role in QPalette.ColorRole:
                if role not in (QPalette.ColorRole.NoRole, QPalette.ColorRole.NColorRoles):
                    self.assertEqual(left.brush(group, role), right.brush(group, role), f"{group.name}/{role.name}")

    def test_default_uses_native_style_and_does_not_write_preferences(self):
        with patch.object(self.manager, "apply") as apply:
            self.manager.restore()
        apply.assert_not_called()
        self.assertEqual(self.app.style().objectName(), self.native_style)
        self.assert_palette_equal(self.app.palette(), self.native_palette)
        self.assertFalse(self.path.exists())

    def test_all_palettes_and_system_restore_all_roles(self):
        for key in (key for key in THEMES if key != 'system'):
            self.manager.apply(key)
            self.app.processEvents()
            self.assertEqual(self.app.style().objectName(), "fusion")
            self.assert_palette_equal(self.app.palette(), theme_palette(THEMES[key]))
        self.manager.apply("system")
        self.app.processEvents()
        self.assertEqual(self.app.style().objectName(), self.native_style)
        self.assert_palette_equal(self.app.palette(), self.native_palette)

    def test_native_palette_updates_are_preserved_in_system_mode(self):
        palette = palette_snapshot(self.app.palette())
        palette.setColor(QPalette.ColorRole.Link, QColor("#123456"))
        self.app.setPalette(palette)
        self.app.processEvents()
        self.manager.apply("dark")
        self.manager.apply("system")
        self.assertEqual(self.app.palette().color(QPalette.ColorRole.Link), QColor("#123456"))

    def test_preview_cancel_and_window_close_restore_without_writing(self):
        for key, close_method in ((key, method) for key in THEMES if key != 'system' for method in ('reject', 'close')):
            dialog = SettingsDialog(manager=self.manager)
            dialog.show()
            dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData(key))
            self.assertEqual(self.manager.current, key)
            self.assertFalse(self.path.exists())
            getattr(dialog, close_method)()
            self.assertEqual(self.manager.current, "system")
            self.assert_palette_equal(self.app.palette(), self.native_palette)
            self.assertFalse(self.path.exists())
            dialog.deleteLater()

    def test_apply_persists_and_cancel_only_discards_subsequent_preview(self):
        dialog = SettingsDialog(manager=self.manager)
        dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("dark"))
        self.assertTrue(dialog.save())
        dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("light"))
        dialog.reject()
        self.assertEqual(self.manager.current, "dark")
        self.assertEqual(QSettings(str(self.path), QSettings.Format.IniFormat).value("appearance/theme"), "dark")
        self.manager.apply("system")
        self.manager.restore()
        self.assertEqual(self.manager.current, "dark")
        dialog.deleteLater()

    def test_save_and_close_restores_saved_choice_on_next_manager(self):
        dialog = SettingsDialog(manager=self.manager)
        dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("light"))
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.manager.apply("system")
        next_manager = ThemeManager(self.app, QSettings(str(self.path), QSettings.Format.IniFormat))
        try:
            next_manager.restore()
            self.assertEqual(next_manager.current, "light")
            self.assert_palette_equal(self.app.palette(), theme_palette(THEMES["light"]))
        finally:
            self.app.removeEventFilter(next_manager)
            next_manager.deleteLater()
            dialog.deleteLater()

    def test_unknown_or_malformed_saved_theme_falls_back_without_deleting_other_settings(self):
        for value in ("removed-theme", ["dark", "light"]):
            self.settings.setValue("appearance/theme", value)
            self.settings.setValue("future/provider", "keep")
            self.settings.sync()
            self.manager.restore()
            self.assertEqual(self.manager.current, "system")
            self.assertTrue(self.manager.warning)
            dialog = SettingsDialog(manager=self.manager)
            dialog.accept()
            self.assertEqual(self.settings.value("appearance/theme"), "system")
            self.assertEqual(self.settings.value("future/provider"), "keep")
            dialog.deleteLater()

    def test_failed_persistence_keeps_dialog_open_and_cancel_available(self):
        blocked = self.root / "blocked"
        blocked.write_text("file, not a directory")
        self.manager.settings = QSettings(str(blocked / "settings.ini"), QSettings.Format.IniFormat)
        dialog = SettingsDialog(manager=self.manager)
        dialog.show()
        dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("dark"))
        dialog.accept()
        self.assertTrue(dialog.isVisible())
        self.assertIn("Não foi possível salvar", dialog.feedback.text())
        dialog.reject()
        self.assertEqual(self.manager.current, "system")
        self.assertEqual(blocked.read_text(), "file, not a directory")
        dialog.deleteLater()

    def test_unknown_theme_rejected_before_changing_application(self):
        with self.assertRaises(ValueError):
            self.manager.apply("unknown")
        self.assert_palette_equal(self.app.palette(), self.native_palette)

    def test_live_windows_status_rows_log_and_progress_keep_state(self):
        window = MainWindow(auto_devices=False)
        self.addCleanup(window.close)
        source = make_iso(self.root / "Example.iso")
        window.load_isos([str(source)])
        self.assertTrue(window.pool.waitForDone(5000))
        self.app.processEvents()
        table = window.iso_table
        table.selectRow(0)
        values = window._selected(table)
        dialog = GameDialog(values, "ISO", window.details.toPlainText())
        self.addCleanup(dialog.close)
        window.show()
        dialog.show()
        window.progress_bar.setValue(375)
        window.overall_progress.setValue(625)
        window.log_view.clear()
        window.log("ERROR", "Erro de exemplo")
        for key in (key for key in THEMES if key != 'system'):
            self.manager.apply(key)
            self.app.processEvents()
            expected = theme_palette(THEMES[key]).color(QPalette.ColorRole.Base)
            self.assertEqual(window.palette().color(QPalette.ColorRole.Base), expected)
            self.assertEqual(dialog.palette().color(QPalette.ColorRole.Base), expected)
            self.assertEqual(window._selected(table), values)
            self.assertEqual(window.progress_bar.value(), 375)
            self.assertEqual(window.overall_progress.value(), 625)
            formats = window.log_view.document().firstBlock().layout().formats()
            self.assertTrue(formats)
            self.assertEqual(formats[0].format.foreground().color(), status_color("error", self.app.palette()))
            for severity in ("ok", "warning", "error"):
                table.item(0, 0).setData(STATUS_ROLE, severity)
                for selected in (False, True):
                    option = QStyleOptionViewItem()
                    option.initFrom(table)
                    if selected:
                        option.state |= QStyle.StateFlag.State_Selected
                    else:
                        option.state &= ~QStyle.StateFlag.State_Selected
                    table.itemDelegate().initStyleOption(option, table.model().index(0, 2))
                    self.assertGreaterEqual(contrast(option.palette.color(QPalette.ColorRole.Text),
                                                     option.backgroundBrush.color()), 4.5)

    def test_builtin_text_selection_and_log_colors_have_readable_contrast(self):
        for key in (key for key in THEMES if key != 'system'):
            palette = theme_palette(THEMES[key])
            for foreground, background in (("Text", "Base"), ("WindowText", "Window"),
                                           ("ButtonText", "Button"), ("HighlightedText", "Highlight"),
                                           ("ToolTipText", "ToolTipBase"), ("PlaceholderText", "Base"),
                                           ("Text", "AlternateBase"), ("Link", "Base"), ("LinkVisited", "Base")):
                self.assertGreaterEqual(contrast(palette.color(getattr(QPalette.ColorRole, foreground)),
                                                palette.color(getattr(QPalette.ColorRole, background))), 4.5)
            for severity in ("ok", "warning", "error"):
                self.assertGreaterEqual(contrast(status_color(severity, palette),
                                                palette.color(QPalette.ColorRole.Base)), 4.5)

    def test_menu_opens_correct_settings_tab(self):
        window = MainWindow(auto_devices=False)
        self.addCleanup(window.close)
        opened = []
        with patch("opldeck.ui.settings_gui.theme_manager", return_value=self.manager), \
                patch.object(SettingsDialog, "exec", lambda dialog: opened.append(dialog.tabs.currentIndex())):
            window.appearance_action.trigger()
            window.providers_action.trigger()
        self.assertEqual(opened, [0, 1])
