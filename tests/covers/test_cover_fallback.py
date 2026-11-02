import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import patch

try:
    from PySide6.QtCore import Qt
    from PySide6.QtNetwork import QNetworkReply
    from PySide6.QtWidgets import QApplication, QMessageBox
    from opldeck.covers.cover_fallback import BackgroundFallback, AlternativeSourcesDialog
    from opldeck.covers.cover_preload import CoverKey
    from opldeck.covers.cover_network import CoverFetcher
    from opldeck.ui.game_gui import GameDialog
    from opldeck.covers.providers import ProviderPreferences, custom_provider
    from tests.covers import test_cover_preload as helpers
    from tests.covers.test_cover_network import Manager
except ModuleNotFoundError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'PySide6 não instalado')
class CoverFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        helpers.CoverPreloadTests.setUp(self)
        self.first = custom_provider('Fonte A', 'https://a.example/{id}.jpg')
        self.second = custom_provider('Fonte B', 'https://b.example/{serial}.png')
        self.set_sources((self.first, self.second))
        self.router = BackgroundFallback(self.service)
        self.addCleanup(self.router.reset)

    if QApplication is not None:
        cleanup = helpers.CoverPreloadTests.cleanup
        wait = helpers.CoverPreloadTests.wait
        finish = helpers.CoverPreloadTests.finish
        start_network = helpers.CoverPreloadTests.start_network

    def set_sources(self, custom, selected='xlenore'):
        for key, value in ProviderPreferences(True, selected, custom).values().items():
            self.settings.setValue(key, value)
        self.settings.sync()
        self.service.sync_preferences()

    def missing_primary(self):
        self.start_network()
        self.manager.reply.headers(404)
        self.wait(lambda: self.router.dialog is not None)
        return self.router.dialog

    def manual(self):
        self.manual_manager = Manager()
        fetcher = CoverFetcher(manager=self.manual_manager)
        self.addCleanup(fetcher.cancel)
        dialog = GameDialog(self.values, 'ISO', '', settings=self.settings, fetcher=fetcher)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_selected_source_success_does_not_consult_or_offer_other_sources(self):
        self.set_sources((self.first, self.second), self.second.key)
        self.start_network()
        self.assertEqual(self.manager.requests[0].url().host(), 'b.example')
        self.finish()
        self.app.processEvents()
        self.assertEqual(len(self.manager.requests), 1)
        self.assertIsNone(self.router.dialog)
        self.assertEqual(self.service.peek(self.key).download.provider_name, 'Fonte B')

    def test_multiple_alternatives_wait_for_choice_and_use_only_marked_source(self):
        dialog = self.missing_primary()
        self.app.processEvents()
        self.assertTrue(self.service.awaiting_choice)
        self.assertEqual(len(self.manager.requests), 1)
        self.assertEqual(dialog.selected_sources(), (self.first.key,))
        dialog.accept()
        self.wait(lambda: len(self.manager.requests) == 2)
        self.assertEqual(self.manager.requests[-1].url().host(), 'a.example')
        self.finish()
        self.assertEqual(self.service.peek(self.key).download.provider_id, self.first.key)
        self.assertFalse((self.library / 'ART').exists())

    def test_selected_alternatives_are_sequential_and_stop_on_success(self):
        dialog = self.missing_primary()
        dialog.sources.item(1).setCheckState(Qt.CheckState.Checked)
        dialog.accept()
        self.wait(lambda: len(self.manager.requests) == 2)
        self.manager.reply.headers(404)
        self.wait(lambda: len(self.manager.requests) == 3)
        self.assertEqual(self.manager.requests[-1].url().host(), 'b.example')
        self.finish()
        self.assertEqual(self.service.peek(self.key).attempted, ('xlenore', self.first.key, self.second.key))
        self.assertIsNone(self.router.dialog)

    def test_single_alternative_is_automatic_only_after_primary_404(self):
        self.set_sources((self.first,))
        self.start_network()
        self.assertEqual(len(self.manager.requests), 1)
        self.assertEqual(self.manager.requests[0].url().host(), 'raw.githubusercontent.com')
        self.manager.reply.headers(404)
        self.wait(lambda: len(self.manager.requests) == 2)
        self.assertIsNone(self.router.dialog)
        self.assertEqual(self.manager.requests[-1].url().host(), 'a.example')
        self.manager.reply.headers(404)
        self.app.processEvents()
        self.assertEqual(len(self.manager.requests), 2)
        self.assertTrue(self.service.peek(self.key).not_found)

    def test_no_alternatives_does_not_prompt(self):
        self.set_sources(())
        self.start_network()
        self.manager.reply.headers(404)
        self.app.processEvents()
        self.assertIsNone(self.router.dialog)
        self.assertEqual(len(self.manager.requests), 1)

    def test_decline_applies_to_remaining_missing_games_until_rescan(self):
        self.missing_primary().reject()
        other = CoverKey.make(self.library, 'SLUS_111.11')
        self.start_network([other])
        self.manager.reply.headers(404)
        self.app.processEvents()
        self.assertEqual(len(self.manager.requests), 2)
        self.assertIsNone(self.router.dialog)
        self.service.reset()
        self.missing_primary().reject()

    def test_authorized_sources_apply_to_next_missing_game_in_same_read(self):
        self.missing_primary().accept()
        self.wait(lambda: len(self.manager.requests) == 2)
        self.finish()
        other = CoverKey.make(self.library, 'SLUS_111.11')
        self.start_network([other])
        self.assertEqual(self.manager.requests[-1].url().host(), 'raw.githubusercontent.com')
        self.manager.reply.headers(404)
        self.wait(lambda: len(self.manager.requests) == 4)
        self.assertIsNone(self.router.dialog)
        self.assertEqual(self.manager.requests[-1].url().host(), 'a.example')
        self.finish()

    def test_http_and_connection_errors_never_trigger_fallback(self):
        for status in (403, 429, 500, None):
            self.service.reset()
            old_count = len(self.manager.requests)
            self.start_network()
            if status:
                self.manager.reply.headers(status)
            else:
                self.manager.reply.finish(QNetworkReply.NetworkError.HostNotFoundError)
            self.app.processEvents()
            self.assertIsNone(self.router.dialog)
            self.assertEqual(len(self.manager.requests), old_count + 1)

    def test_library_change_closes_prompt_and_old_confirmation_cannot_fetch(self):
        dialog = self.missing_primary()
        self.service.reset()
        dialog.accept()
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())
        self.assertIsNone(self.router.dialog)
        self.assertFalse(self.service.awaiting_choice)
        self.assertEqual(len(self.manager.requests), 1)

    def test_source_removal_invalidates_prompt_and_cache(self):
        dialog = self.missing_primary()
        self.set_sources((self.second,))
        self.assertFalse(dialog.isVisible())
        self.assertIsNone(self.router.dialog)
        self.assertFalse(self.service.cache)
        self.assertFalse(self.service.awaiting_choice)

    def test_manual_single_alternative_uses_same_policy(self):
        self.set_sources((self.first,))
        dialog = self.manual()
        dialog.search_cover()
        with patch.object(AlternativeSourcesDialog, 'exec', side_effect=AssertionError('Unexpected prompt')):
            self.manual_manager.reply.headers(404)
        self.assertEqual(len(self.manual_manager.requests), 2)
        self.assertEqual(self.manual_manager.requests[-1].url().host(), 'a.example')
        self.finish(self.manual_manager.reply)
        self.assertEqual(dialog.pending_source, self.first.name)
        self.assertTrue(dialog.save_button.isEnabled())

    def test_manual_decline_does_not_consult_alternatives(self):
        dialog = self.manual()
        dialog.search_cover()
        with patch.object(AlternativeSourcesDialog, 'exec', return_value=AlternativeSourcesDialog.DialogCode.Rejected):
            self.manual_manager.reply.headers(404)
        self.assertEqual(len(self.manual_manager.requests), 1)
        self.assertIsNone(dialog.pending_image)

    def test_manual_cancel_during_fallback_preserves_previous_preview(self):
        dialog = self.manual()
        dialog.pending_image = self.image
        dialog.reload_cover()
        with patch('opldeck.ui.game_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            dialog.search_cover()
        def choose(window):
            window.sources.item(1).setCheckState(Qt.CheckState.Checked)
            window.accept()
            return window.result()
        with patch.object(AlternativeSourcesDialog, 'exec', choose):
            self.manual_manager.reply.headers(404)
        self.assertEqual(len(self.manual_manager.requests), 2)
        dialog.fetcher.cancel()
        self.assertEqual(len(self.manual_manager.requests), 2)
        self.assertIs(dialog.pending_image, self.image)
        self.assertTrue(dialog.save_button.isEnabled())

    def test_dialog_cannot_confirm_empty_selection(self):
        dialog = self.missing_primary()
        dialog.sources.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(dialog.search.isEnabled())
        dialog.accept()
        self.assertTrue(dialog.isVisible())
        self.assertEqual(len(self.manager.requests), 1)
        dialog.reject()

    def test_library_invalidation_cancels_manual_search_without_fallback(self):
        dialog = self.manual()
        dialog.search_cover()
        old = self.manual_manager.reply
        dialog.preload_invalidated()
        old.headers(404)
        self.assertTrue(old.aborted)
        self.assertEqual(len(self.manual_manager.requests), 1)
        self.assertIsNone(dialog.search_preferences)
        self.assertFalse(dialog.save_button.isEnabled())

    def test_duplicate_serials_share_alternative_result(self):
        self.set_sources((self.first,))
        other = CoverKey.make(self.library, self.key.game_id, 'Outro nome', 'Outra ISO')
        self.start_network([self.key, other])
        self.wait(lambda: self.service.worker is None and not self.service.local_queue)
        self.manager.reply.headers(404)
        self.wait(lambda: len(self.manager.requests) == 2)
        self.finish()
        self.app.processEvents()
        self.assertEqual(len(self.manager.requests), 2)
        self.assertEqual(self.service.peek(other).download.provider_id, self.first.key)

    def test_library_invalidation_while_manual_chooser_is_open_ignores_answer(self):
        dialog = self.manual()
        dialog.search_cover()
        def choose(window):
            dialog.preload_invalidated()
            window.accept()
            return window.result()
        with patch.object(AlternativeSourcesDialog, 'exec', choose):
            self.manual_manager.reply.headers(404)
        self.assertEqual(len(self.manual_manager.requests), 1)
        self.assertIsNone(dialog.search_preferences)
        self.assertFalse(dialog.save_button.isEnabled())
