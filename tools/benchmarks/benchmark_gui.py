"""Mede atualizações da interface com linhas sintéticas, sem acessar jogos/USB.

QT_QPA_PLATFORM=offscreen python3 -m tools.benchmarks.benchmark_gui --rows 2000 --repeat 3
"""

import argparse
import json
import platform
from statistics import median
import tempfile
from time import perf_counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6 import __version__ as qt_version
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from opldeck.covers.cover_preload import CoverPreloader
from opldeck.ui.gui import MainWindow


def benchmark(rows, repeat):
    app = QApplication.instance() or QApplication([])
    samples = {name: [] for name in ('populate_ms', 'actions_20_ms', 'search_6_ms', 'select_all_ms')}
    data = [
        ['Identificado', f'SLUS_{index // 100:03}.{index % 100:02}', f'Jogo {index:05}',
         'DVD', '1.00 GiB', '1', f'/benchmark/DVD/Jogo {index:05}.iso', '1073741824',
         '12345678', 'ISO9660 · SYSTEM.CNF / BOOT2', 'Pasta DVD/']
        for index in range(rows)
    ]
    with tempfile.TemporaryDirectory() as directory:
        settings = QSettings(str(Path(directory) / 'settings.ini'), QSettings.Format.IniFormat)
        window = MainWindow(auto_devices=False, cover_preloader=CoverPreloader(settings=settings))
        window.import_monitor.timer.stop()
        window.storage_timer.stop()
        window.show()
        app.processEvents()
        table = window.iso_table
        search = window.table_controls[table][0]
        for _ in range(repeat):
            window.clear_table(table)
            window.log_view.clear()
            start = perf_counter()
            window.append_rows(table, data)
            window.filter_table(table)
            app.processEvents()
            samples['populate_ms'].append((perf_counter() - start) * 1000)
            start = perf_counter()
            for _ in range(20):
                window._update_actions()
            samples['actions_20_ms'].append((perf_counter() - start) * 1000)
            start = perf_counter()
            for query in ('j', 'jo', 'jogo', 'jogo 00', 'ausente', ''):
                search.setText(query)
            app.processEvents()
            samples['search_6_ms'].append((perf_counter() - start) * 1000)
            start = perf_counter()
            table.selectAll()
            app.processEvents()
            samples['select_all_ms'].append((perf_counter() - start) * 1000)
        window.close()
    return {'rows': rows, 'repeat': repeat, 'python': platform.python_version(), 'pyside6': qt_version,
            'platform': app.platformName(), 'samples': samples,
            'median_ms': {name: round(median(values), 3) for name, values in samples.items()}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=2000)
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.rows <= 100_000 or args.repeat < 1:
        parser.error('Use 1 a 100.000 linhas e pelo menos uma repetição.')
    print(json.dumps(benchmark(args.rows, args.repeat), indent=2))
