"""Cria AppDir/AppImage local sem baixar ferramentas ou alterar a versão."""

import argparse
import importlib.metadata
from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run(command, **kwargs):
    subprocess.run(list(map(str, command)), check=True, cwd=ROOT, **kwargs)


def write_launcher(appdir):
    launcher = appdir / 'AppRun'
    launcher.write_text('#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                        'exec "$HERE/usr/lib/opl-deck/opl-deck" "$@"\n')
    launcher.chmod(0o755)
    return launcher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'appimage-build')
    parser.add_argument('--appimagetool', type=Path, help='Executável local obtido do projeto AppImage')
    parser.add_argument('--appdir-only', action='store_true')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.python_implementation() != 'CPython':
        parser.error('Use Linux com CPython.')
    for dependency in ('pyinstaller', 'py7zr', 'PySide6'):
        try:
            importlib.metadata.version(dependency)
        except importlib.metadata.PackageNotFoundError:
            parser.error(f'Dependência ausente: {dependency}. Instale .[appimage] em um ambiente virtual.')
    tool = args.appimagetool or shutil.which('appimagetool')
    if not args.appdir_only and not tool:
        parser.error('Informe --appimagetool ou use --appdir-only.')
    run([sys.executable, '-m', 'pip', 'check'])
    from opldeck.library.archive_7z import library
    library()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    appdir = output / 'OPLDeck.AppDir'
    appdir.mkdir()
    command = [sys.executable, '-m', 'PyInstaller', '--onedir', '--name', 'opl-deck',
               '--distpath', appdir / 'usr' / 'lib', '--workpath', output / 'work',
               '--specpath', output, '--paths', ROOT,
               '--recursive-copy-metadata', 'py7zr', '--copy-metadata', 'PySide6']
    # Os codecs são carregados dinamicamente: incluí-los explicitamente evita
    # um pacote que funciona apenas enquanto 7z está instalado na máquina.
    modules = ['py7zr', 'inflate64', 'multivolumefile', 'bcj', 'pyppmd',
               'Cryptodome', 'psutil', 'brotli', 'texttable']
    modules += ['backports.zstd'] if sys.version_info < (3, 14) else ['compression.zstd']
    for module in modules:
        command.extend(['--collect-all', module])
    command.append(ROOT / 'tools' / 'packaging' / 'packaging_entry.py')
    run(command)
    launcher = write_launcher(appdir)
    (appdir / 'opl-deck.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=OPL Deck\nExec=opl-deck\n'
        'Icon=opl-deck\nCategories=Utility;\nTerminal=false\n')
    (appdir / 'opl-deck.svg').write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">'
        '<rect x="4" y="4" width="120" height="120" rx="20" fill="#336c9e"/>'
        '<circle cx="64" cy="64" r="42" fill="#e8edf2"/>'
        '<circle cx="64" cy="64" r="11" fill="#336c9e"/></svg>')
    notices = appdir / 'usr' / 'share' / 'doc' / 'opl-deck'
    notices.mkdir(parents=True)
    shutil.copy2(ROOT / 'LICENSE', notices)
    shutil.copy2(Path(sys.base_prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'LICENSE.txt',
                 notices / 'LICENSE-Python.txt')
    for document in ('README.md', 'CHANGELOG.md'):
        shutil.copy2(ROOT / document, notices)
    if (ROOT / 'docs').is_dir():
        shutil.copytree(ROOT / 'docs', notices / 'docs')
    # Inclui textos de licença e metadados fornecidos por cada distribuição.
    from tools.packaging.license_inventory import collect
    collect(notices / 'dependencies')
    run([launcher, '--self-test-archives'])
    run([launcher, '--self-test-gui'])
    run([launcher, '--version'])
    if args.appdir_only:
        print(appdir)
        return
    from opldeck import __version__
    artifact = output / f'OPLDeck-{__version__}-{platform.machine()}.AppImage'
    run([Path(tool).resolve(), appdir, artifact])
    print(artifact)


if __name__ == '__main__':
    main()
