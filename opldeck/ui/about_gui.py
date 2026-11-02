"""Informações da versão e acesso às páginas públicas do projeto."""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from opldeck.branding import CHANGELOG_URL, ISSUES_URL, LICENSE_URL, REPOSITORY_URL

ABOUT_TEXT = (
    "# OPL Deck\n\n"
    "**Desenvolvido por Petrus (@petru-sdev)**\n\n"
    "O OPL Deck é um gerenciador de jogos de PlayStation 2 para Linux, desenvolvido em Python e PySide6. "
    "Seu objetivo é simplificar a preparação, organização e transferência de jogos para uso com o Open PS2 Loader (OPL).\n\n"
    "### Recursos\n\n"
    "- **Conversão de formatos:** conversão entre ISO e UL, além de BIN/CUE para ISO.\n"
    "- **Importação de arquivos compactados:** suporte a arquivos ZIP e 7z.\n"
    "- **Gerenciamento de biblioteca:** organização e controle dos jogos armazenados.\n"
    "- **Detecção de duplicados:** identificação de jogos repetidos por meio de seus IDs, inclusive entre formatos ISO e UL.\n"
    "- **Gerenciamento de capas:** busca online, importação local e obtenção de capas em lote.\n"
    "- **Transferências:** envio de jogos em fila, acompanhamento do progresso e nova tentativa de transferências não concluídas.\n"
    "- **Personalização:** configuração de temas, fontes e capas.\n"
    "- **Estimativa de tempo:** exibição da duração das operações em segundos, minutos, horas ou dias.\n\n"
    "**Inspiração:** OPLUtil, desenvolvido por Issei Yoshida.\n\n"
    "**GitHub:** [https://github.com/petru-sdev/OPLDeck](https://github.com/petru-sdev/OPLDeck)"
)


class AboutPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        self.description = QLabel(ABOUT_TEXT)
        self.description.setTextFormat(Qt.TextFormat.MarkdownText)
        self.description.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.description.setOpenExternalLinks(True)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        links = QHBoxLayout()
        self.link_buttons = {}
        for label, url in (('Código-fonte', REPOSITORY_URL), ('Relatar problema', ISSUES_URL),
                           ('Notas da versão', CHANGELOG_URL), ('Licença', LICENSE_URL)):
            button = QPushButton(label)
            button.setToolTip(url)
            button.clicked.connect(lambda checked=False, target=url: QDesktopServices.openUrl(QUrl(target)))
            self.link_buttons[label] = button
            links.addWidget(button)
        links.addStretch()
        layout.addLayout(links)
        layout.addStretch()
