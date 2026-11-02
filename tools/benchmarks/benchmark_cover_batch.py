"""Mede a seleção de capas preparadas, sem rede ou jogos reais.

QT_QPA_PLATFORM=offscreen python3 -m tools.benchmarks.benchmark_cover_batch --rows 1000 --repeat 3
"""

import argparse
import json
from pathlib import Path
import platform
from statistics import median
import sys
import tempfile
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6 import __version__ as qt_version
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from opldeck.covers.artwork import CoverState
from opldeck.covers.cover_batch import CoverSaveJob
from opldeck.ui.cover_batch_gui import CoverBatchDialog
from opldeck.covers.cover_preload import CoverKey


def benchmark(rows, repeat):
    app = QApplication.instance() or QApplication([])
    samples = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
        keys = [CoverKey.make(root, f'SLUS_{i // 100:03}.{i % 100:02}', f'Game {i}') for i in range(rows)]
        dialog = CoverBatchDialog(keys, settings=settings)
        # Measure only selection updates on a prepared batch; no background I/O.
        dialog.timer.stop()
        dialog.preparing = False
        dialog.jobs = {i: CoverSaveJob(key, root / 'preview.png',
                      CoverState(root / 'ART' / f'{key.game_id}_COV.png', None), True)
                       for i, key in enumerate(keys)}
        dialog.update_selection()
        for _ in range(repeat):
            start = perf_counter()
            dialog.preserve.setChecked(False)
            assert len(dialog.selected_rows()) == rows
            dialog.preserve.setChecked(True)
            assert not dialog.selected_rows()
            samples.append((perf_counter() - start) * 1000)
        dialog.reject()
        app.processEvents()
        dialog.deleteLater()
    return {'rows': rows, 'repeat': repeat, 'python': platform.python_version(),
            'pyside6': qt_version, 'platform': app.platformName(),
            'selection_toggle_ms': samples, 'median_ms': round(median(samples), 3)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=1000)
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.rows <= 100_000 or args.repeat < 1:
        parser.error('Use 1 a 100.000 linhas e pelo menos uma repetição.')
    print(json.dumps(benchmark(args.rows, args.repeat), indent=2))
