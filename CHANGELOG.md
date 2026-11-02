# Histórico de versões

## 0.8.0 — 28/10/2026

- AppImage Linux x86_64 construído em Debian 12, com Python/Qt/7z incluídos, autoteste da interface e receita Docker. [Construção e limites](docs/compilacao-linux.md).
- Revisão para publicação: confirmação de capas revalidada após alteração/fechamento da biblioteca, leitura de pendrive adiada durante janelas modais e fila iniciada após a confirmação de limpeza. Resultados de capas já gravadas são preservados após uma exceção inesperada.
- Seleção em lotes grandes de capas otimizada: atualização conjunta dos controles e prévia recarregada somente quando o jogo selecionado recebe sua capa. [Achados e medição](docs/revisao-publicacao.md).

- Cinco temas opcionais: Meia-noite, Floresta, Ametista, Areia e Alto contraste, com amostras de cores no seletor e prévia reversível. Sistema permanece como padrão. [Prévias e uso](docs/temas.md).

- Fila de transferências visível para instalações, exportações e conversões. Estados por jogo, cancelamento e nova tentativa somente dos não concluídos.
- Ordem ajustável na revisão do lote, preservando o título editado e a escolha de mídia de cada jogo.
- Detecção de IDs duplicados entre as listas ISO e UL, com caminhos e registros, sem exclusão automática.
- Salvamento de capas em lote com prévias, seleção, preservação das existentes por padrão, confirmação de substituição e resultados por jogo.
- Durações em segundos, minutos, horas e dias, incluindo estimativa de tempo restante.
- Aba Sobre atualizada com versão e links para código-fonte, problemas, notas da versão e licença.
- Metadados, documentação pública e pacote de fontes preparados para a entrega.

A fila é mantida apenas na sessão e reinicia os arquivos não concluídos desde o começo. Duplicidade usa o ID, sem comparar conteúdo. Capas só são gravadas após a ação explícita de salvar. Download de jogos permanece indisponível.

Consulte o [manual dos recursos](docs/fila-duplicados-capas.md) e as [notas, testes e limitações](docs/versao-0.9.0.md). Esta entrada registra a versão do código e a compilação local; não afirma que o release já foi publicado.

## Etapa 0.8.0 — outubro de 2026

Base da entrega: aparência personalizável, leitura automática de pendrive, ficha do jogo, capas locais/online, fontes cadastráveis, importação ZIP/7z e Pasta de import, limpeza de extraídos após verificação, progresso por jogo/lote e botão Buscar imports. O nome público passou a ser OPL Deck, preservando o comando e as configurações antigos.

Revisões posteriores incluíram limites de leitura e abertura de arquivos regulares, bloqueio de py7zr vulnerável, capas PNG proporcionais, diagnóstico de nomes/pastas ISO, busca com acentos/aliases de ID, ordenação numérica e diagnóstico de partes UL. Os registros técnicos anteriores permanecem em `docs/`.
