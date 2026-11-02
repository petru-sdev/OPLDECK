"""Abertura de arquivos de dados sem seguir links nem bloquear em FIFOs."""

from contextlib import contextmanager
import os
from pathlib import Path
import stat

from opldeck.core.formats import FormatError


@contextmanager
def regular_reader(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise FormatError(f'Esperado arquivo regular: {path}')
        stream = os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream
