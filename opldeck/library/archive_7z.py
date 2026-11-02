"""7z por fluxo: o programa cria os arquivos; o extrator não escolhe destinos."""

import io
import lzma
import os
import re
import selectors
import shutil
import subprocess
import sys
import zlib

from opldeck.core.conversion import BUFFER_SIZE, _check_cancel
from opldeck.core.formats import FormatError


class PasswordRequired(FormatError):
    def __init__(self):
        super().__init__('O arquivo é protegido. Informe a senha para continuar.')


class WrongPassword(FormatError):
    def __init__(self):
        super().__init__('Senha incorreta ou dados criptografados corrompidos. Confira a senha e tente novamente.')


def library():
    try:
        import py7zr
    except ImportError:
        raise FormatError('Suporte 7z indisponível: instale a dependência py7zr ou use o AppImage completo.') from None
    # Header parsing happens before our member limits can run. Versions through
    # 1.1.3 have upstream header-allocation and zero-progress vulnerabilities.
    version = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:\.post\d+)?', str(getattr(py7zr, '__version__', '')))
    if not version or not (1, 1, 4) <= tuple(map(int, version.groups())) < (1, 2, 0):
        raise FormatError('Suporte 7z bloqueado: é necessário py7zr>=1.1.4,<1.2 com correções de segurança. '
                          'Atualize as dependências do OPL Deck. ZIP continua disponível.')
    return py7zr


class CancellableReader(io.IOBase):
    def __init__(self, stream, cancel):
        self.stream, self.cancel = stream, cancel

    def read(self, size=-1):
        _check_cancel(self.cancel)
        return self.stream.read(size)

    def seek(self, *args):
        _check_cancel(self.cancel)
        return self.stream.seek(*args)

    def tell(self):
        return self.stream.tell()

    def readable(self):
        return True

    def seekable(self):
        return True


def open_7z(stream, password=None, *, cancel=None, limit=32 << 30):
    py7zr = library()
    stream.seek(0)
    try:
        return py7zr.SevenZipFile(CancellableReader(stream, cancel), password=password, max_extract_size=limit)
    except py7zr.exceptions.PasswordRequired:
        raise PasswordRequired() from None
    except (py7zr.exceptions.Bad7zFile, ValueError, TypeError, EOFError, lzma.LZMAError, zlib.error) as exc:
        if password is not None:
            raise WrongPassword() from None
        raise FormatError('Cabeçalho 7z inválido ou corrompido.') from None


def backend(requested='auto', *, encrypted=False):
    executable = shutil.which('7zz') or shutil.which('7z')
    if requested not in ('auto', 'system', 'py7zr'):
        raise ValueError('Extrator 7z desconhecido.')
    # Senhas nunca entram em argv, ambiente, arquivos ou saída do subprocesso.
    if encrypted or requested == 'py7zr':
        return 'py7zr'
    if executable:
        return executable
    if requested == 'system':
        raise FormatError('7z/7zz não está instalado no sistema.')
    return 'py7zr'


def native_extract(stream, names, writers, executable, *, cancel=None):
    for name in names:
        _check_cancel(cancel)
        writer = writers(name)
        descriptor = stream.fileno()
        command = [executable, 'x', '-so', '-spd', '-bd', '-bb0', '-bso0', '-bse2', '-bsp0',
                   '-y', '-t7z', '--', f'/proc/self/fd/{descriptor}', name]
        environment = None
        if getattr(sys, 'frozen', False):
            # PyInstaller altera o caminho de bibliotecas para seu próprio Qt.
            # O 7z externo deve usar as bibliotecas do sistema.
            environment = os.environ.copy()
            original = environment.pop('LD_LIBRARY_PATH_ORIG', None)
            if original is None:
                environment.pop('LD_LIBRARY_PATH', None)
            else:
                environment['LD_LIBRARY_PATH'] = original
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, pass_fds=(descriptor,), env=environment)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, True)
                selector.register(process.stderr, selectors.EVENT_READ, False)
                while selector.get_map():
                    _check_cancel(cancel)
                    for key, _ in selector.select(.1):
                        data = os.read(key.fileobj.fileno(), BUFFER_SIZE)
                        if not data:
                            selector.unregister(key.fileobj)
                        elif key.data:
                            writer.write(data)
                        # stderr is drained, never copied to the log (untrusted output).
                if process.wait() != 0:
                    raise FormatError('Falha ao extrair 7z: arquivo corrompido ou método não suportado.')
            writer.flush()
            writer.close()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()


def python_extract(stream, names, writers, password=None, *, cancel=None, limit=32 << 30):
    py7zr = library()
    class Factory(py7zr.io.WriterFactory):
        def create(self, filename):
            return writers(filename)
    try:
        with open_7z(stream, password, cancel=cancel, limit=limit) as archive:
            archive.extract(targets=list(names), factory=Factory())
    except py7zr.exceptions.PasswordRequired:
        raise PasswordRequired() from None
    except FormatError:
        raise
    except (py7zr.exceptions.Bad7zFile, py7zr.exceptions.DecompressionError, py7zr.exceptions.CrcError,
            EOFError, ValueError, lzma.LZMAError, zlib.error) as exc:
        if password is not None:
            raise WrongPassword() from None
        raise FormatError('Dados 7z corrompidos ou incompletos.') from None


def extract(stream, names, writers, selected_backend, password=None, *, cancel=None, limit=32 << 30):
    if selected_backend == 'py7zr':
        python_extract(stream, names, writers, password, cancel=cancel, limit=limit)
    else:
        native_extract(stream, names, writers, selected_backend, cancel=cancel)
