"""Busca assíncrona de uma capa em memória, sem gravar arquivos da biblioteca."""

from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from opldeck import __version__
from opldeck.covers.artwork import MAX_IMAGE_BYTES, decode_image, prepare_cover
from opldeck.covers.providers import PROVIDERS

REQUEST_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class DownloadedCover:
    image: object
    game_id: str
    provider_id: str
    byte_count: int
    provider_name: str = ''


class CoverFetcher(QObject):
    progress = Signal(int, int)
    finished = Signal(object, str)

    def __init__(self, parent=None, *, manager=None, providers=None):
        super().__init__(parent)
        self.manager = manager if manager is not None else QNetworkAccessManager(self)
        self.providers = dict(PROVIDERS if providers is None else providers)
        self.reply = None
        self.data = bytearray()
        self.game_id = ""
        self.provider_id = ""
        self.last_http_status = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self._complete(None, "Tempo de busca esgotado. Tente novamente."))

    @property
    def busy(self):
        return self.reply is not None

    def start(self, provider_id, game_id):
        if self.busy:
            raise ValueError("Uma busca de capa já está em andamento.")
        if provider_id not in self.providers:
            raise ValueError("Provedor de capas desconhecido.")
        provider = self.providers[provider_id]
        url = provider.cover_url(game_id)
        self.provider_name = provider.name
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", f"OPL-Deck/{__version__}".encode("ascii"))
        request.setRawHeader(b"Accept", b"image/jpeg, image/png")
        request.setRawHeader(b"Accept-Encoding", b"identity")
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        self.game_id, self.provider_id = game_id, provider_id
        self.last_http_status = None
        self.data.clear()
        reply = self.manager.get(request)
        self.reply = reply
        reply.metaDataChanged.connect(lambda: self._headers(reply))
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finished(reply))
        self.timer.start(REQUEST_TIMEOUT_MS)
        self.progress.emit(0, 0)

    def _headers(self, reply):
        if reply is not self.reply:
            return False
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status is not None and status != 200:
            message = {
                404: "Capa não encontrada para este ID no provedor.",
                429: "O provedor limitou as consultas. Aguarde antes de tentar novamente.",
                401: "O provedor recusou o acesso à capa.",
                403: "O provedor recusou o acesso à capa.",
            }.get(status, "O endereço da capa foi redirecionado; verifique o provedor." if 300 <= status < 400
                  else f"Falha no provedor de capas (HTTP {status}).")
            self._complete(None, message)
            return False
        length = bytes(reply.rawHeader("Content-Length"))
        if length:
            try:
                count = int(length)
            except ValueError:
                self._complete(None, "O provedor enviou um tamanho de imagem inválido.")
                return False
            if not 0 <= count <= MAX_IMAGE_BYTES:
                self._complete(None, "A capa excede o limite de 20 MiB ou informa tamanho inválido.")
                return False
        return True

    def _read(self, reply):
        if not self._headers(reply):
            return
        self.data.extend(bytes(reply.read(MAX_IMAGE_BYTES + 1 - len(self.data))))
        if len(self.data) > MAX_IMAGE_BYTES:
            self._complete(None, "A capa excede o limite de 20 MiB.")
            return
        total = bytes(reply.rawHeader("Content-Length"))
        self.progress.emit(len(self.data), int(total) if total else 0)

    def _finished(self, reply):
        if not self._headers(reply):
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._complete(None, "Não foi possível obter a capa. Verifique a conexão e tente novamente.\n"
                           + reply.errorString())
            return
        if reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200:
            self._complete(None, "O provedor não confirmou o recebimento da capa.")
            return
        self._read(reply)
        if reply is not self.reply:
            return
        total = bytes(reply.rawHeader("Content-Length"))
        if total and len(self.data) != int(total):
            self._complete(None, "A imagem recebida está incompleta. Tente novamente.")
            return
        try:
            image = prepare_cover(decode_image(bytes(self.data)))
        except ValueError as exc:
            self._complete(None, f"Imagem inválida recebida do provedor: {exc}")
            return
        self._complete(DownloadedCover(image, self.game_id, self.provider_id, len(self.data), self.provider_name), "")

    def cancel(self):
        if self.busy:
            self._complete(None, "Busca de capa cancelada.")

    def _complete(self, result, error):
        reply = self.reply
        if reply is None:
            return
        self.reply = None  # Sinais tardios, inclusive do abort(), não substituem a próxima busca.
        self.last_http_status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        self.timer.stop()
        self.data.clear()
        if not reply.isFinished():
            reply.abort()
        reply.deleteLater()
        self.finished.emit(result, error)
