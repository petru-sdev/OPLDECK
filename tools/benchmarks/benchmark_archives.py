"""Compara extratores sem modificar o arquivo original; limpa somente sua área de teste."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opldeck.library.archives import extract_archives, list_archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', choices=('system', 'py7zr', 'both'), default='both')
    args = parser.parse_args()
    results = []
    for backend in ('system', 'py7zr') if args.backend == 'both' else (args.backend,):
        plan = list_archive(args.archive, backend=backend)
        with tempfile.TemporaryDirectory(prefix='.opldeck-benchmark-', dir=args.workdir) as directory:
            start = time.monotonic()
            result = extract_archives([args.archive], directory, plans=[plan])
            elapsed = time.monotonic() - start
            hashes = []
            for path in result.paths:
                with path.open('rb') as stream:
                    hashes.append(hashlib.file_digest(stream, 'sha256').hexdigest())
            item = {'backend': backend, 'bytes': sum(result.sizes), 'seconds': elapsed,
                    'MiB_per_second': sum(result.sizes) / elapsed / (1 << 20), 'sha256': hashes}
            results.append(item)
            print(json.dumps(item), flush=True)
    if len(results) == 2 and results[0]['sha256'] != results[1]['sha256']:
        raise RuntimeError('Os extratores produziram resultados diferentes.')
    args.output.write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
