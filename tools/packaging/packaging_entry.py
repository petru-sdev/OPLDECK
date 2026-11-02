"""Entrada congelada; o autoteste comprova o fallback sem um 7z instalado."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def smoke_archives():
    import json
    from pathlib import Path
    import tempfile
    from unittest.mock import patch
    import zipfile
    from opldeck.library.archive_7z import library
    from opldeck.library.archives import extract_archives, list_archive

    py7zr = library()  # Refuse vulnerable versions before the first archive is opened.
    payload = b'OPL Deck packaged archive smoke test\n' * 4096
    with tempfile.TemporaryDirectory(prefix='opldeck-package-test-') as temporary:
        root = Path(temporary)
        destination = root / 'out'
        destination.mkdir()
        zip_path = root / 'zip.data'
        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('test.iso', payload)
        cases = [(zip_path, None)]
        for suffix, password in [('plain', None), ('encrypted', 'synthetic-package-test')]:
            source = root / suffix
            with py7zr.SevenZipFile(source, 'w', password=password, header_encryption=bool(password)) as archive:
                archive.writestr(payload, 'test.iso')
            cases.append((source, password))
        with patch('opldeck.library.archive_7z.shutil.which', return_value=None):
            for source, password in cases:
                plan = list_archive(source, password=password)
                if plan.kind == '7z' and plan.backend != 'py7zr':
                    raise RuntimeError('Fallback py7zr não selecionado.')
                result = extract_archives([source], destination, plans=[plan])
                if result.paths[0].read_bytes() != payload:
                    raise RuntimeError('Conteúdo extraído divergente.')
    print(json.dumps({'zip': 'ok', '7z_fallback': 'ok', '7z_password': 'ok', 'py7zr': py7zr.__version__}))


def smoke_gui():
    import json
    import os
    from pathlib import Path
    import tempfile

    # A packaging check must not read or change the user's preferences/devices.
    with tempfile.TemporaryDirectory(prefix='opl-deck-gui-test-') as temporary:
        os.environ['XDG_CONFIG_HOME'] = str(Path(temporary) / 'config')
        os.environ['XDG_CACHE_HOME'] = str(Path(temporary) / 'cache')
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from opldeck import __version__
        from opldeck.ui.gui import MainWindow

        app = QApplication([])
        window = MainWindow(auto_devices=False)
        window.show()
        observed = []
        def inspect_window():
            observed.append(window.isVisible() and window.tabs.count() >= 5)
            app.quit()
        QTimer.singleShot(250, inspect_window)
        if app.exec() != 0 or observed != [True]:
            raise RuntimeError('Falha ao iniciar a interface empacotada.')
        result = {'gui': 'ok', 'version': __version__, 'title': window.windowTitle(),
                  'tabs': [window.tabs.tabText(i) for i in range(window.tabs.count())],
                  'platform': app.platformName()}
        window.close()
        app.processEvents()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test-archives']:
        smoke_archives()
    elif sys.argv[1:] == ['--self-test-gui']:
        smoke_gui()
    else:
        from opldeck.__main__ import main
        raise SystemExit(main())
