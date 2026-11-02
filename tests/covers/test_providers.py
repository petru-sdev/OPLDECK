import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from opldeck.covers.providers import PROVIDERS, ProviderPreferences, custom_provider, validate_catalog

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from opldeck.covers.cover_network import CoverFetcher
    from opldeck.ui.provider_gui import ProviderDialog
    from opldeck.ui.settings_gui import SettingsDialog
    from opldeck.ui.themes import ThemeManager
    from tests.covers.test_cover_network import Manager
except ModuleNotFoundError:
    QApplication = None


class ProviderValidationTests(unittest.TestCase):
    def test_id_and_serial_templates(self):
        provider = custom_provider('Minha fonte', 'https://covers.example/{id}/{serial}.png')
        self.assertEqual(provider.cover_url('SLUS_207.18'),
                         'https://covers.example/SLUS_207.18/SLUS-20718.png')
        self.assertEqual(provider.homepage, 'https://covers.example')

    def test_invalid_templates_are_rejected(self):
        for template in ('file:///tmp/{id}.jpg', 'http://covers.example/{id}.jpg',
                         'https://user:password@covers.example/{id}.jpg',
                         'https://{id}.example/cover.png', 'https://covers.example/cover.png',
                         'https://covers.example/{name}.jpg', 'https://covers.example/{id.__class__}',
                         'https://covers.example/{id!r}', 'https://covers.example/{id:20}',
                         'https://covers.example/{id', 'https://covers.example/{id}.jpg#fragment',
                         'https://covers.example:invalid/{id}', 'https://covers.example/{id} space',
                         'https://covers.example\\evil/{id}'):
            with self.subTest(template=template), self.assertRaises(ValueError):
                custom_provider('Fonte', template)

    def test_duplicate_name_url_and_key_are_rejected(self):
        first = custom_provider('Fonte', 'https://one.example/{id}')
        for other in (custom_provider('fonte', 'https://two.example/{id}'),
                      custom_provider('Outra', first.template),
                      custom_provider('Outra', 'https://two.example/{id}', key=first.key)):
            with self.assertRaises(ValueError):
                validate_catalog((first, other))
        with self.assertRaises(ValueError):
            validate_catalog((custom_provider('Outra', PROVIDERS['xlenore'].template),))


@unittest.skipIf(QApplication is None, 'PySide6 não instalado')
class ProviderSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.manager = ThemeManager(self.app, self.settings)
        self.addCleanup(self.manager.deleteLater)
        self.addCleanup(lambda: self.app.removeEventFilter(self.manager))
        self.provider = custom_provider('Fonte de teste', 'https://covers.example/{serial}.png')

    def dialog(self):
        dialog = SettingsDialog(manager=self.manager, initial_tab='providers')
        self.addCleanup(dialog.deleteLater)
        return dialog

    def add(self, dialog):
        def edit(editor):
            editor.name.setText(self.provider.name)
            editor.template.setText(self.provider.template)
            editor.accept()
            return editor.result()
        with patch.object(ProviderDialog, 'exec', edit):
            dialog.add_provider()

    def test_add_cancel_does_not_write_and_apply_persists_selected_source(self):
        dialog = self.dialog()
        self.add(dialog)
        self.assertFalse(self.settings.contains('providers/custom'))
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.reject()
        self.assertFalse(ProviderPreferences.load(self.settings).custom)
        dialog = self.dialog()
        self.add(dialog)
        dialog.provider_enabled.setChecked(True)
        self.assertTrue(dialog.save())
        restored = ProviderPreferences.load(QSettings(self.settings.fileName(), QSettings.Format.IniFormat))
        self.assertEqual(restored.catalog[restored.provider_id].template, self.provider.template)
        self.assertTrue(restored.enabled)

    def test_edit_preserves_key_and_remove_returns_to_builtin(self):
        dialog = self.dialog()
        self.add(dialog)
        key = dialog.provider_combo.currentData()
        def edit(editor):
            editor.name.setText('Renomeada')
            editor.template.setText('https://other.example/{id}.jpg')
            editor.accept()
            return editor.result()
        with patch.object(ProviderDialog, 'exec', edit):
            dialog.edit_provider()
        self.assertEqual(dialog.provider_combo.currentData(), key)
        self.assertEqual(dialog.selected_provider().catalog[key].name, 'Renomeada')
        with patch('opldeck.ui.settings_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            dialog.remove_provider()
        self.assertEqual(dialog.provider_combo.currentData(), 'xlenore')
        self.assertFalse(dialog.custom_providers)
        self.assertFalse(dialog.edit_provider_button.isEnabled())

    def test_failed_write_does_not_replace_saved_catalog(self):
        dialog = self.dialog()
        self.add(dialog)
        with patch.object(self.manager, 'save', side_effect=OSError('falha')):
            self.assertFalse(dialog.save())
        self.assertFalse(ProviderPreferences.load(self.settings).custom)
        self.assertTrue(dialog.apply_button.isEnabled())

    def test_malformed_catalog_does_not_enable_network(self):
        self.settings.setValue('providers/enabled', True)
        for raw in ('broken json', '{}', json.dumps([{'key': 'xlenore', 'name': 'Fake', 'template': self.provider.template}])):
            self.settings.setValue('providers/custom', raw)
            self.assertFalse(ProviderPreferences.load(self.settings).enabled)

    def test_selected_custom_source_is_only_request(self):
        prefs = ProviderPreferences(True, self.provider.key, (self.provider,))
        for key, value in prefs.values().items():
            self.settings.setValue(key, value)
        restored = ProviderPreferences.load(self.settings)
        manager = Manager()
        fetcher = CoverFetcher(manager=manager, providers=restored.catalog)
        self.addCleanup(fetcher.cancel)
        fetcher.start(restored.provider_id, 'SLUS_207.18')
        self.assertEqual(len(manager.requests), 1)
        self.assertEqual(manager.requests[0].url().toString(), 'https://covers.example/SLUS-20718.png')

    def test_editor_invalid_url_cannot_be_accepted(self):
        editor = ProviderDialog()
        self.addCleanup(editor.deleteLater)
        editor.name.setText('Fonte')
        editor.template.setText('https://covers.example/search')
        editor.accept()
        self.assertNotEqual(editor.result(), QDialog.DialogCode.Accepted)
        self.assertIsNone(editor.provider)
