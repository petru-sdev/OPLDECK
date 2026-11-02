"""Feedback visual de atividade, independente das operações em disco."""

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from opldeck.ui.themes import status_color
from opldeck.core.duration import duration_text


class ActivityIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 34)
        self.state = 'idle'
        self.angle = 0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = (status_color(self.state, self.palette()) if self.state in ('ok', 'warning', 'error')
                 else self.palette().color(QPalette.ColorRole.Highlight))
        painter.setPen(QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.state == 'busy':
            painter.drawArc(5, 5, 24, 24, -self.angle * 16, 260 * 16)
        elif self.state == 'ok':
            painter.drawLine(7, 17, 14, 24)
            painter.drawLine(14, 24, 27, 10)
        else:
            painter.drawEllipse(5, 5, 24, 24)
            if self.state == 'error':
                painter.drawLine(12, 12, 22, 22)
                painter.drawLine(22, 12, 12, 22)
            elif self.state == 'warning':
                painter.drawLine(17, 10, 17, 18)
                painter.drawPoint(17, 24)
            else:
                painter.drawLine(17, 10, 17, 17)
                painter.drawLine(17, 17, 23, 17)


class ActivityPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setBackgroundRole(QPalette.ColorRole.Base)
        self.setAutoFillBackground(True)
        self.result = None
        self.cancelling = False
        self.started = 0
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(12, 10, 12, 10)
        heading = QHBoxLayout()
        self.indicator = ActivityIndicator(self)
        heading.addWidget(self.indicator)
        self.title = QLabel('Pronto para começar')
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        font = self.title.font()
        font.setPointSize(font.pointSize() + 2)
        font.setBold(True)
        self.title.setFont(font)
        heading.addWidget(self.title, 1)
        self.elapsed = QLabel()
        heading.addWidget(self.elapsed)
        self.content_layout.addLayout(heading)
        self.message = QLabel('Adicione jogos ou conecte um pendrive para começar.')
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.content_layout.addWidget(self.message)
        self.previous_result = QLabel()
        self.previous_result.setTextFormat(Qt.TextFormat.PlainText)
        self.previous_result.setWordWrap(True)
        self.previous_result.hide()
        self.content_layout.addWidget(self.previous_result)
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.animate)

    def animate(self):
        self.indicator.angle = (self.indicator.angle + 24) % 360
        self.indicator.update()
        self.elapsed.setText(f'Em andamento · {duration_text(time.monotonic() - self.started)}')

    def begin(self, title, message, *, scan=False):
        self.cancelling = False
        if not scan:
            self.result = None
        self.previous_result.setText(self.result[0] if self.result else '')
        self.previous_result.setVisible(bool(self.result))
        self.title.setText(title)
        self.message.setText(message)
        self.indicator.state = 'busy'
        self.indicator.setAccessibleName('Operação em andamento')
        self.started = time.monotonic()
        self.animate()
        self.timer.start()

    def phase(self, message):
        if not self.cancelling and self.timer.isActive():
            self.message.setText(message)

    def cancel_pending(self):
        self.cancelling = True
        self.title.setText('Cancelando operação…')
        self.message.setText('Aguarde a confirmação. Finalizando a etapa atual e limpando arquivos parciais.')

    def finish(self, title, message, severity='ok', *, remember=False):
        self.timer.stop()
        self.cancelling = False
        self.title.setText(title)
        self.message.setText(message)
        self.elapsed.setText('Cancelado' if severity == 'warning' else 'Interrompido' if severity == 'error' else 'Finalizado')
        self.indicator.state = severity
        self.indicator.setAccessibleName(self.elapsed.text())
        self.indicator.update()
        self.previous_result.hide()
        if remember:
            self.result = (title, message, severity)

    def finish_scan(self, message, error=False):
        if error:
            self.finish('Não foi possível concluir a leitura', message, 'error')
            self.previous_result.setVisible(bool(self.result))
        elif self.result:
            title, detail, severity = self.result
            self.finish(title, detail + '\n' + message, severity)
        else:
            self.finish('Leitura concluída', message)
