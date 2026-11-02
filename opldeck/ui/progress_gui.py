"""Cores das etapas limitadas às barras, independentes do tema global."""

from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QProgressBar, QStyle, QStyleFactory, QStyleOptionProgressBar


class OperationProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.extraction = False
        self.progress_style = QStyleFactory.create('Fusion')

    def set_extraction(self, extraction):
        self.extraction = extraction
        self.update()

    def paintEvent(self, event):
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)
        option.palette.setColor(QPalette.ColorRole.Highlight, QColor('#e4b824' if self.extraction else '#296aa3'))
        option.palette.setColor(QPalette.ColorRole.HighlightedText, QColor('#202020' if self.extraction else '#ffffff'))
        painter = QPainter(self)
        self.progress_style.drawControl(QStyle.ControlElement.CE_ProgressBar, option, painter, self)
