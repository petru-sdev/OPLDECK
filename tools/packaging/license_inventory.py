"""Guarda metadados e textos de licença das dependências instaladas."""

import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys


def collect(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    names = ['py7zr', 'inflate64', 'multivolumefile', 'pybcj', 'pyppmd',
             'pycryptodomex', 'psutil', 'brotli', 'texttable', 'PySide6', 'shiboken6']
    if sys.version_info < (3, 14):
        names.append('backports.zstd')
    for optional in ('PySide6-Essentials', 'PySide6-Addons', 'pyinstaller'):
        try:
            metadata.distribution(optional)
        except metadata.PackageNotFoundError:
            continue
        names.append(optional)
    result = []
    for name in names:
        dist = metadata.distribution(name)
        target = destination / name
        target.mkdir()
        (target / 'METADATA.txt').write_text(dist.read_text('METADATA') or '', encoding='utf-8')
        files = []
        for item in dist.files or ():
            if any(word in str(item).lower() for word in ('license', 'copying', 'copyright', 'notice')):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    # Paths originate in package metadata; never mirror ../.
                    output = target / f'{len(files):03}-{source.name}'
                    shutil.copy2(source, output)
                    files.append(output.name)
        result.append({'name': name, 'version': dist.version,
                       'license': dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                       'license_files': files})
    (destination / 'inventory.json').write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    collect(parser.parse_args().destination)
