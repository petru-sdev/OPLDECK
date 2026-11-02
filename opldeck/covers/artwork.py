"""Capas locais: leitura limitada, prévia Qt e publicação na pasta ART."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter

from opldeck.core.conversion import _regular_reader, _snapshot, _sync_directory, directory_lock, require_clean_library
from opldeck.core.formats import FormatError, validate_game_id
from opldeck.library.management import _move

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_SIDE = 4096
# Conservative publication envelope: at most 1 MiB for a 32-bit GS texture,
# below OPL's 720 * 512 * 4 budget. Inputs retain their separate safety limits.
OPL_COVER_SIDE = 512


@dataclass(frozen=True)
class CoverState:
    path: Path
    fingerprint: tuple | None
    origin: Path | None = None
    origin_fingerprint: tuple | None = None


@dataclass(frozen=True)
class LoadedCover:
    state: CoverState | None
    image: QImage | None
    path: Path | None
    message: str
    needs_save: bool = False


def library_root(path: str | Path, kind: str) -> Path | None:
    parent = Path(path).absolute().parent
    if kind == "UL":
        return parent
    if kind == "ISO" and parent.name.upper() in {"CD", "DVD"}:
        return parent.parent
    return None


def image_bytes(path: str | Path) -> bytes:
    with _regular_reader(Path(path)) as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise FormatError("A imagem excede o limite de 20 MiB.")
    return data


def decode_image(data: bytes) -> QImage:
    if len(data) > MAX_IMAGE_BYTES:
        raise FormatError("A imagem excede o limite de 20 MiB.")
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    if bytes(reader.format()).lower() not in {b"jpeg", b"jpg", b"png"}:
        raise FormatError("Escolha uma imagem JPEG ou PNG válida.")
    size = reader.size()
    if not size.isValid() or max(size.width(), size.height()) > MAX_IMAGE_SIDE:
        raise FormatError("A imagem deve ter até 4096 pixels em cada dimensão.")
    image = reader.read()
    if image.isNull():
        raise FormatError(f"Não foi possível ler a imagem: {reader.errorString()}")
    return image


def cover_state(root: str | Path, game_id: str) -> CoverState:
    validate_game_id(game_id)
    root = Path(root).absolute()
    if not root.is_dir():
        raise FormatError("Selecione uma raiz de biblioteca existente.")
    art = art_directory(root)
    existing = _cover_candidates(art, game_id)
    return _cover_state(art, game_id, existing)[0]


def _cover_state(art, game_id, existing):
    """Return the save guard and the same bytes used to build it for preview."""
    target = art / f'{game_id}_COV.png'
    canonical_exists = os.path.lexists(target)
    if not canonical_exists and len(existing) > 1:
        raise FormatError("Existem várias capas para este ID. Mantenha apenas uma antes de editar: "
                          + ', '.join(path.name for path in existing))
    if not canonical_exists and not existing:
        return CoverState(target, None), None
    path = target if canonical_exists else existing[0]
    with _regular_reader(path) as stream:
        before = _snapshot(os.fstat(stream.fileno()))
        data = stream.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise FormatError("A capa existente excede 20 MiB; verifique o arquivo antes de editar.")
        if before != _snapshot(os.fstat(stream.fileno())):
            raise FormatError("A capa mudou durante a leitura. Abra a ficha novamente.")
    fingerprint = (before, hashlib.sha256(data).hexdigest())
    return (CoverState(target, fingerprint) if canonical_exists else
            CoverState(target, None, path, fingerprint)), data


def art_directory(root: str | Path) -> Path:
    root = Path(root)
    folders = [path for path in root.iterdir() if path.name.casefold() == 'art']
    if len(folders) > 1:
        raise FormatError('Há mais de uma pasta ART/art. Mantenha uma única pasta de capas.')
    art = folders[0] if folders else root / 'ART'
    if art.is_symlink() or (art.exists() and not art.is_dir()):
        raise FormatError('ART deve ser uma pasta, sem link simbólico.')
    return art


def cover_candidates(root: str | Path, game_id: str, *, title='', filename='') -> list[Path]:
    """ID exato primeiro; nomes legados só servem à leitura, nunca à substituição."""
    validate_game_id(game_id)
    art = art_directory(root)
    return _cover_candidates(art, game_id, title=title, filename=filename)


def _cover_ids(game_id):
    return [game_id.casefold(), game_id.replace('_', '-').replace('.', '').casefold()]


def _cover_candidates(art, game_id, *, title='', filename=''):
    if not art.exists():
        return []
    ids = _cover_ids(game_id)
    names = [name.casefold() for name in (title, filename) if name and '/' not in name and '\\' not in name]
    candidates = []
    for path in art.iterdir():
        stem = path.stem.casefold()
        if path.suffix.casefold() not in {'.jpg', '.jpeg', '.png'} or not stem.endswith('_cov'):
            continue
        base = stem[:-4]
        if base in ids:
            rank = ids.index(base)
        elif base in names:
            rank = 2 + names.index(base)
        else:
            continue
        # A canonical PNG may coexist with the preserved JPG/legacy source.
        candidates.append((-1 if path.name == f'{game_id}_COV.png' else rank, path.name.casefold(), path))
    return [item[2] for item in sorted(candidates, key=lambda item: item[:2])]


def load_cover(root: str | Path, game_id: str, *, title='', filename='') -> LoadedCover:
    """Scan once and reuse the fingerprinted bytes to display an existing cover."""
    validate_game_id(game_id)
    root = Path(root).absolute()
    if not root.is_dir():
        raise FormatError("Selecione uma raiz de biblioteca existente.")
    art = art_directory(root)
    candidates = _cover_candidates(art, game_id, title=title, filename=filename)
    existing = [path for path in candidates if path.stem[:-4].casefold() in _cover_ids(game_id)]
    state, data, state_error = None, None, ''
    try:
        state, data = _cover_state(art, game_id, existing)
    except (OSError, ValueError) as exc:
        state_error = str(exc)
    failures = []
    image, cover_path, needs_save = None, None, False
    for path in candidates:
        try:
            raw = data if state and path == (state.origin or state.path) and data is not None else image_bytes(path)
            original = decode_image(raw)
            image = prepare_cover(original)
        except (OSError, ValueError) as exc:
            failures.append(f"{path.name}: {exc}")
            continue
        cover_path = path
        message = f"Capa encontrada: {path}"
        needs_save = bool(state and (path != state.path or not raw.startswith(b'\x89PNG\r\n\x1a\n')
                                    or original.size() != image.size()))
        if state and path != state.path:
            message += f"\nNome legado: a prévia será salva em {state.path.name}; a origem {path.name} será preservada."
        if needs_save:
            message += f'\nPrévia preparada em PNG, {image.width()} × {image.height()} pixels. Use Salvar alterações para gravar.'
        break
    else:
        message = ("Pasta de capas não encontrada: " if not art.exists() else "Nenhuma capa correspondente em: ") + str(art)
        message += f"\nNome para o OPL: {game_id}_COV.png. JPG/JPEG são aceitos como origem."
    if state_error:
        message += "\n" + state_error
    if failures:
        message += "\n" + "\n".join(failures)
    return LoadedCover(state, image, cover_path, message, needs_save)


def prepare_cover(image: QImage) -> QImage:
    """Prepare a proportional preview; never upscale, crop or modify the input."""
    if image.isNull() or max(image.width(), image.height()) > MAX_IMAGE_SIDE:
        raise FormatError('Imagem inválida ou maior que 4096 pixels.')
    if max(image.width(), image.height()) <= OPL_COVER_SIDE:
        return image
    return image.scaled(OPL_COVER_SIDE, OPL_COVER_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def encode_image(image: QImage, suffix: str) -> bytes:
    if image.isNull() or max(image.width(), image.height()) > MAX_IMAGE_SIDE:
        raise FormatError("Imagem inválida ou maior que 4096 pixels.")
    jpeg = suffix.lower() in {'.jpg', '.jpeg'}
    if jpeg:
        # JPEG não tem canal alfa: transparência recebe fundo branco.
        flattened = QImage(image.size(), QImage.Format.Format_RGB32)
        flattened.fill(Qt.GlobalColor.white)
        painter = QPainter(flattened)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flattened
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "JPEG" if jpeg else "PNG", 95):
        raise FormatError("Falha ao preparar a capa para gravação.")
    data = bytes(buffer.data())
    if len(data) > MAX_IMAGE_BYTES:
        raise FormatError("A capa preparada excede 20 MiB.")
    return data


def save_cover(root: str | Path, game_id: str, image: QImage, expected: CoverState) -> Path:
    """Substituição só se a capa ainda corresponder à prévia confirmada."""
    root = Path(root).absolute()
    prepared = prepare_cover(image)
    # Publish RGB/RGBA PNG, independent of the source encoding or palette depth.
    prepared = prepared.convertToFormat(QImage.Format.Format_RGBA8888 if prepared.hasAlphaChannel()
                                        else QImage.Format.Format_RGB888)
    data = encode_image(prepared, '.png')
    with directory_lock(root) as root_fd:
        require_clean_library(root)
        if cover_state(root, game_id) != expected:
            raise FormatError("A capa mudou desde a prévia. Abra a ficha novamente antes de salvar.")
        art = art_directory(root)
        art.mkdir(exist_ok=True)
        with directory_lock(art) as art_fd:
            descriptor, name = tempfile.mkstemp(prefix=".opldeck-cover-", dir=art)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if cover_state(root, game_id) != expected:
                    raise FormatError("A capa mudou durante a gravação; a alteração externa foi preservada.")
                if expected.fingerprint is None:
                    _move(temporary, expected.path)
                else:
                    os.replace(temporary, expected.path)
                try:
                    _sync_directory(art_fd)
                    _sync_directory(root_fd)
                except OSError as exc:
                    exc.add_note("A capa foi publicada, mas a sincronização falhou. Confira o arquivo no destino.")
                    raise
            finally:
                temporary.unlink(missing_ok=True)
    return expected.path
