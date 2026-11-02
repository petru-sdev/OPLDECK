import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from opldeck import __version__
from opldeck.covers.cover_preload import CoverPreloader
from opldeck.ui.gui import MainWindow
from opldeck.ui.settings_gui import SettingsDialog
from opldeck.ui.themes import Personalization, ThemeManager, palette_snapshot
from tests.ui.test_themes import contrast
from tests.fixtures import make_iso


class PersonalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.native_font = QFont(self.app.font())
        self.native_palette = palette_snapshot(self.app.palette())
        self.native_style = self.app.style().objectName()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.manager = ThemeManager(self.app, self.settings)
        with patch('opldeck.ui.gui.theme_manager', return_value=self.manager):
            self.window = MainWindow(auto_devices=False, cover_preloader=CoverPreloader(settings=self.settings))
        self.window.import_monitor.timer.stop()
        self.dialog = SettingsDialog(manager=self.manager)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.dialog.reject()
        self.dialog.deleteLater()
        self.window.close()
        self.window.deleteLater()
        self.app.removeEventFilter(self.manager)
        self.manager.deleteLater()
        self.app.setStyle(self.native_style)
        self.app.setPalette(self.native_palette)
        self.app.setFont(self.native_font)
        self.app.processEvents()
        self.temporary.cleanup()

    def customize(self):
        self.dialog.theme_combo.setCurrentIndex(self.dialog.theme_combo.findData('dark'))
        self.dialog.accent = '#25d6a2'
        self.dialog.accent_enabled.setChecked(True)
        self.dialog.font_scale.setValue(125)
        self.dialog.density_combo.setCurrentIndex(self.dialog.density_combo.findData('comfortable'))
        self.app.processEvents()

    def test_preview_cancel_restores_font_palette_density_and_keeps_table_data(self):
        table = self.window.iso_table
        source = make_iso(self.root / 'Game.iso')
        self.window.load_isos([str(source)])
        self.assertTrue(self.window.pool.waitForDone(5000))
        self.app.processEvents()
        table.selectRow(0)
        before_height = table.verticalHeader().defaultSectionSize()
        self.customize()
        self.assertEqual(self.manager.personalization, Personalization('#25d6a2', 125, 'comfortable'))
        self.assertGreater(table.verticalHeader().defaultSectionSize(), before_height)
        self.assertTrue(self.dialog.apply_button.isEnabled())
        self.assertFalse((self.root / 'settings.ini').exists())
        self.dialog.reject()
        self.app.processEvents()
        self.assertEqual(self.app.font(), self.native_font)
        self.assertEqual(self.manager.personalization, Personalization())
        self.assertEqual(self.app.palette().color(QPalette.ColorRole.Highlight), self.native_palette.color(QPalette.ColorRole.Highlight))
        self.assertEqual(table.verticalHeader().defaultSectionSize(), before_height)
        self.assertEqual(table.item(0, 1).text(), 'SLUS_123.45')
        self.assertEqual([i.row() for i in table.selectionModel().selectedRows()], [0])

    def test_save_restart_and_reset_cancel_preserve_last_saved_personalization(self):
        self.settings.setValue('future/provider', 'keep-existing-source-setting')
        self.customize()
        self.assertTrue(self.dialog.save())
        saved = self.manager.personalization
        self.dialog.reset_appearance.click()
        self.assertEqual(self.manager.current, 'system')
        self.assertEqual(self.manager.personalization, Personalization())
        self.dialog.reject()
        self.assertEqual(self.manager.personalization, saved)
        self.manager.apply('system', Personalization())
        self.manager.restore()
        self.assertEqual(self.manager.current, 'dark')
        self.assertEqual(self.manager.personalization, saved)
        self.assertEqual(self.settings.value('future/provider'), 'keep-existing-source-setting')
        self.dialog.reset_appearance.click()
        self.assertTrue(self.dialog.save())
        self.manager.restore()
        self.assertEqual(self.manager.personalization, Personalization())
        self.assertEqual(self.manager.current, 'system')

    def test_font_scale_does_not_accumulate_when_changing_theme(self):
        for key in ('dark', 'light', 'system', 'dark'):
            self.manager.apply(key, Personalization('', 150, 'compact'))
            self.app.processEvents()
            if self.native_font.pointSizeF() > 0:
                self.assertAlmostEqual(self.app.font().pointSizeF(), self.native_font.pointSizeF() * 1.5, places=1)
            else:
                self.assertEqual(self.app.font().pixelSize(), round(self.native_font.pixelSize() * 1.5))
            table = self.window.iso_table
            self.assertGreaterEqual(table.verticalHeader().defaultSectionSize(), table.fontMetrics().height() + 8)

    def test_accent_text_contrast_and_disabled_palette_remain_readable(self):
        self.manager.apply('dark')
        disabled = self.app.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight)
        for color in ('#ffffff', '#000000', '#ffff00', '#777777', '#00ff88'):
            self.manager.apply('dark', Personalization(color))
            palette = self.app.palette()
            self.assertGreaterEqual(contrast(palette.color(QPalette.ColorRole.Highlight),
                                            palette.color(QPalette.ColorRole.HighlightedText)), 4.5)
            self.assertEqual(palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight), disabled)

    def test_invalid_saved_options_fall_back_without_deleting_unrelated_preferences(self):
        for key, value in [('appearance/accent', '#ff000088'), ('appearance/font_scale', 500),
                           ('appearance/density', ['bad']), ('appearance/font_scale', True)]:
            self.settings.clear()
            self.settings.setValue('import/folder', '/keep-this-folder')
            self.settings.setValue(key, value)
            self.manager.restore()
            self.assertEqual(self.manager.personalization, Personalization())
            self.assertTrue(self.manager.warning)
            self.assertEqual(self.settings.value('import/folder'), '/keep-this-folder')

    def test_failed_save_can_be_cancelled_and_color_picker_cancel_keeps_previous_color(self):
        with patch('opldeck.ui.settings_gui.QColorDialog.getColor', return_value=QColor()):
            previous = self.dialog.accent
            self.dialog.choose_accent()
            self.assertEqual(self.dialog.accent, previous)
        blocked = self.root / 'blocked'
        blocked.write_text('not a folder')
        self.manager.settings = QSettings(str(blocked / 'settings.ini'), QSettings.Format.IniFormat)
        self.customize()
        self.dialog.accept()
        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn('Não foi possível salvar', self.dialog.feedback.text())
        self.dialog.reject()
        self.assertEqual(self.app.font(), self.native_font)
        self.assertEqual(self.manager.personalization, Personalization())

    def test_new_name_and_legacy_cli_are_consistent(self):
        self.assertEqual(self.window.windowTitle(), f'OPL Deck · {__version__}')
        self.assertTrue(any(label.text() == 'OPL Deck' for label in self.window.findChildren(QLabel)))
        result = subprocess.run([sys.executable, '-m', 'opldeck', '--version'], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), __version__)
        help_text = subprocess.run([sys.executable, '-m', 'opldeck', '--help'], capture_output=True, text=True, check=True)
        self.assertIn('OPL Deck', help_text.stdout)
