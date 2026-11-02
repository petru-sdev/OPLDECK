"""Descoberta de volumes USB montados, sem montar ou modificar dispositivos."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


@dataclass(frozen=True)
class Volume:
    root: Path
    device: str
    label: str
    mount_id: str
    readonly: bool = False

    @property
    def key(self):
        return (str(self.root), self.device, self.mount_id)


def mounts():
    """Mount ID distingue uma nova montagem no mesmo caminho do volume anterior."""
    result = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        separator = fields.index('-')
        unescape = lambda value: re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
        result[unescape(fields[4])] = (fields[0], unescape(fields[separator + 2]),
                                      'ro' in fields[5].split(','))
    return result


def parse_volumes(data, mounted):
    volumes = {}
    unmounted = set()

    def visit(node, usb=False):
        usb = usb or node.get('tran') == 'usb' or node.get('rm') in (True, 1, '1')
        children = node.get('children', [])
        paths = node.get('mountpoints') or []
        if usb and node.get('type') in {'disk', 'part'}:
            for path in paths:
                if not path or path not in mounted or not path.startswith('/') or path == '/':
                    continue
                mount_id, device, readonly = mounted[path]
                if device != node.get('path'):
                    continue
                volume = Volume(Path(path), device, node.get('label') or Path(path).name,
                                mount_id, readonly or node.get('ro') in (True, 1, '1'))
                volumes[volume.key] = volume
            if not children and not any(path in mounted for path in paths if path):
                unmounted.add(node.get('path'))
        for child in children:
            visit(child, usb)

    for node in data['blockdevices']:
        visit(node)
    return tuple(sorted(volumes.values(), key=lambda v: str(v.root))), len(unmounted)


def volume_available(volume, *, write=False):
    try:
        current = mounts().get(str(volume.root))
        if current is None or current[:2] != (volume.mount_id, volume.device):
            return False
        if not volume.root.is_dir():
            return False
        return not write or (not volume.readonly and not current[2]
                             and os.access(volume.root, os.W_OK)
                             and not os.statvfs(volume.root).f_flag & os.ST_RDONLY)
    except OSError:
        return False


def require_volume(volume, *, write=False):
    if not volume_available(volume, write=write):
        raise ValueError('O pendrive foi removido, desmontado ou não permite gravação. Reconecte e aguarde a leitura.')


def library_files(root):
    """Somente raiz e CD/DVD: não percorre lixeira, backups ou pastas do sistema."""
    root = Path(root)
    children = list(root.iterdir())
    indexes = [p for p in children if p.name.casefold() == 'ul.cfg' and not p.is_symlink() and p.is_file()]
    if len(indexes) > 1:
        raise ValueError('Há mais de um índice ul.cfg na raiz. Confira os arquivos antes de continuar.')
    images = [p for p in children if p.suffix.lower() == '.iso' and not p.is_symlink() and p.is_file()]
    for directory in children:
        if directory.name.upper() in {'CD', 'DVD'} and not directory.is_symlink() and directory.is_dir():
            images.extend(p for p in directory.iterdir()
                          if p.suffix.lower() == '.iso' and not p.is_symlink() and p.is_file())
    return (indexes[0] if indexes else None), sorted(map(str, images))


class DeviceMonitor(QObject):
    changed = Signal(object, int)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.finished.connect(self.completed)
        self.process.errorOccurred.connect(self.process_error)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self.process.kill)
        self.running = False
        self.last = None
        self.last_error = ''

    def start(self):
        self.running = True
        self.timer.start()
        self.poll()

    def stop(self):
        self.running = False
        self.timer.stop()
        self.deadline.stop()
        self.process.kill()
        self.process.waitForFinished(1000)

    def poll(self):
        if self.running and self.process.state() == QProcess.ProcessState.NotRunning:
            self.process.start('lsblk', ['--json', '--output', 'NAME,PATH,TYPE,TRAN,RM,MOUNTPOINTS,LABEL,RO'])
            self.deadline.start(5000)

    def report_error(self, message):
        if message != self.last_error:
            self.failed.emit(message)
        self.last_error = message
        self.last = None

    def process_error(self, error):
        if self.running:
            self.report_error('Não foi possível consultar os pendrives: ' + self.process.errorString())

    def completed(self, code, status):
        self.deadline.stop()
        if not self.running:
            return
        if code != 0 or status != QProcess.ExitStatus.NormalExit:
            self.report_error('Falha ao consultar os pendrives. Verifique se lsblk está disponível.')
            return
        try:
            result = parse_volumes(json.loads(bytes(self.process.readAllStandardOutput())), mounts())
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.report_error('Não foi possível ler os dispositivos: ' + str(exc))
            return
        self.last_error = ''
        if result != self.last:
            self.last = result
            self.changed.emit(*result)
