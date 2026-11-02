import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

try:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from opldeck.ui.gui import MainWindow
except ModuleNotFoundError:
    QApplication = None

from opldeck.core.conversion import CancelledError, Progress
from opldeck.core.formats import Media
from tests.fixtures import make_iso


@unittest.skipIf(QApplication is None, 'PySide6 não instalado')
class ActivityGUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.window = MainWindow(auto_devices=False)
        self.window.show()
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        self.window.pool.waitForDone(5000)
        self.app.processEvents()
        self.window.close()

    def wait_worker(self):
        deadline = time.monotonic() + 5
        while self.window.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.window.worker)

    def test_reading_is_visible_and_animated_until_worker_finishes(self):
        release = Event()
        def scan(paths):
            release.wait(3)
            return []
        try:
            self.window._scan(scan, [], self.window.iso_table)
            self.assertEqual(self.window.activity.title.text(), 'Lendo arquivos…')
            self.assertTrue(self.window.activity.isVisible())
            before = self.window.activity.indicator.angle
            QTest.qWait(180)
            self.assertNotEqual(before, self.window.activity.indicator.angle)
            self.assertIsNotNone(self.window.worker)
        finally:
            release.set()
        self.wait_worker()
        self.assertEqual(self.window.activity.title.text(), 'Leitura concluída')
        self.assertFalse(self.window.activity.timer.isActive())

    def test_cancel_pending_survives_late_progress_and_stops_animation(self):
        release = Event()
        def work(*, cancel, progress):
            cancel.wait(3)
            release.wait(3)
            raise CancelledError('Conversão cancelada; arquivos parciais removidos.')
        self.window._convert('Transferência de teste', work, (), {})
        self.assertEqual(self.window.progress_bar.maximum(), 0)
        self.assertTrue(self.window.operation_progress.isVisible())
        try:
            self.window.cancel_button.click()
            self.window._progress(Progress(50, 100, 1, 1))
            self.assertEqual(self.window.activity.title.text(), 'Cancelando operação…')
            self.assertIn('Aguarde a confirmação', self.window.activity.message.text())
            self.assertTrue(self.window.activity.timer.isActive())
        finally:
            release.set()
        self.wait_worker()
        self.assertIn('cancelada', self.window.activity.title.text())
        self.assertEqual(self.window.activity.indicator.state, 'warning')
        self.assertFalse(self.window.activity.timer.isActive())
        self.assertEqual(self.window.progress_bar.maximum(), 1000)

    def test_fast_install_result_remains_visible_after_automatic_refresh(self):
        source = make_iso(self.root / 'Teste.iso')
        destination = self.root / 'usb'
        destination.mkdir()
        self.window.start_install(source, destination, 'Teste', Media.DVD)
        self.wait_worker()
        self.assertEqual(self.window.ul_table.rowCount(), 1)
        self.assertEqual(self.window.activity.title.text(), 'ISO → UL concluída')
        self.assertIn('Lista atualizada', self.window.activity.message.text())
        self.assertEqual(self.window.activity.indicator.state, 'ok')
        self.assertFalse(self.window.activity.timer.isActive())
        self.assertEqual(self.window.progress_bar.value(), 1000)
        self.assertTrue(self.window.activity.isVisible())

    def test_failure_before_first_progress_does_not_leave_busy_indicator(self):
        def fail(*, cancel, progress):
            raise OSError('Falha simulada de leitura')
        with patch('opldeck.ui.gui.QMessageBox.warning'):
            self.window._convert('Operação de teste', fail, (), {})
            self.wait_worker()
        self.assertIn('interrompida', self.window.activity.title.text())
        self.assertIn('Falha simulada', self.window.activity.message.text())
        self.assertEqual(self.window.activity.indicator.state, 'error')
        self.assertFalse(self.window.activity.timer.isActive())
        self.assertEqual(self.window.progress_bar.maximum(), 1000)
        self.assertEqual(self.window.progress_bar.value(), 0)
