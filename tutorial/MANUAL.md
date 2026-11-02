# Manual do Usuário — OPL Deck

Bem-vindo ao manual passo a passo do **OPL Deck** (versão `0.8.0`), gerenciador de jogos de PlayStation 2 para Linux voltado ao **Open PS2 Loader (OPL)**.

Consulte também a [Referência de Uso](referencia-de-uso.md) para detalhes técnicos, especificações de formatos e todos os comandos de terminal, ou volte ao [README principal](../README.md).

---

## Sumário

1. [Requisitos e Instalação](#1-requisitos-e-instalação)
2. [Visão Geral da Interface](#2-visão-geral-da-interface)
3. [Transferir Jogos ISO para o Pendrive](#3-transferir-jogos-iso-para-o-pendrive)
4. [Importar Arquivos Compactados (ZIP e 7z)](#4-importar-arquivos-compactados-zip-e-7z)
5. [Converter Imagens BIN/CUE para ISO](#5-converter-imagens-bincue-para-iso)
6. [Gerenciar Jogos UL no Pendrive ou Biblioteca](#6-gerenciar-jogos-ul-no-pendrive-ou-biblioteca)
7. [Recuperar Operações e Reconstruir `ul.cfg`](#7-recuperar-operações-e-reconstruir-ulcfg)
8. [Capas Locais, Busca Online e Salvamento em Lote](#8-capas-locais-busca-online-e-salvamento-em-lote)
9. [Detectar Jogos Duplicados](#9-detectar-jogos-duplicados)
10. [Fila de Transferências, Histórico e Log](#10-fila-de-transferências-histórico-e-log)
11. [Personalizar Aparência e Temas](#11-personalizar-aparência-e-temas)
12. [Atalhos de Teclado](#12-atalhos-de-teclado)
13. [Créditos e Licença](#13-créditos-e-licença)

---

## 1. Requisitos e Instalação

### Requisitos no Linux
- **Python 3.11 ou superior**
- **PySide6** (`>=6.6,<7`) para a interface gráfica
- **py7zr** (`>=1.1.4,<1.2`) para abrir e extrair arquivos `.7z` (arquivos `.zip` usam a biblioteca padrão do Python)
- **`lsblk`** (`util-linux`, presente por padrão nas distribuições Linux) para descoberta automática de pendrives montados

### Como executar a partir do código-fonte

Na pasta raiz do projeto:

```bash
python3 -m opldeck
```

Para instalar em um ambiente virtual isolado e habilitar os comandos `opldeck` e `opl-deck`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
opldeck
```

---

## 2. Visão Geral da Interface

Ao abrir o **OPL Deck**, a janela principal apresenta:

- **Barra de menus superior**:
  - **Biblioteca**: acesso rápido a *Detectar jogos duplicados…*, *Salvar capas da seleção em lote…* e *Fila de transferências*.
  - **Configurações**: acesso às abas *Aparência…*, *Provedores…*, *Pasta de import…* e *Rever pasta de import*.
  - **Histórico**: abre a janela com todos os jogos já transferidos.
- **Contadores da aba ativa**: exibem o **Total de jogos**, **Jogos íntegros\***, **Jogos com avisos** e **Jogos falhos**.
- **Seletor de Pendrive e barra de armazenamento**: reconhece automaticamente dispositivos USB montados pelo sistema, exibe o espaço livre/usado/total e permite atualizar a leitura com **Reler pendrive**.
- **Painel de atividade**: mostra o andamento da leitura, extração ou transferência atual, tempo decorrido, velocidade em MiB/s e tempo restante estimado.
- **Abas principais**:
  1. **Downloads**: espaço visual reservado (*Download de jogos — Em breve*; busca e download ainda não estão disponíveis nesta versão).
  2. **Imagens ISO**: lista de arquivos `.iso` locais e do pendrive.
  3. **Jogos UL**: lista de jogos divididos no formato USBExtreme (`ul.cfg`).
  4. **Imagens BIN/CUE**: lista de imagens de CD em formato `.bin`/`.cue` para conversão em `.iso`.
  5. **Fila de transferências**: acompanhamento dos itens da transferência atual e repetição de jogos não concluídos.
  6. **Log**: registro detalhado de eventos, avisos e erros da sessão.
  7. **Sobre**: informações do projeto, recursos e links úteis.

---

## 3. Transferir Jogos ISO para o Pendrive

### Fluxo automático (com pendrive conectado)
1. Conecte o pendrive ao computador e aguarde o Linux montá-lo (se necessário, abra-o uma vez no gerenciador de arquivos do sistema).
2. O campo **Pendrive** selecionará o dispositivo automaticamente se houver apenas um montado (ou permita escolhê-lo na lista se houver mais de um).
3. Na aba **Imagens ISO**, clique em **Adicionar ISOs…** (ou pressione `Ctrl+O`) e selecione os arquivos `.iso` no seu computador.
4. Selecione os jogos desejados na tabela (ou deixe sem seleção para incluir todas as ISOs locais visíveis) e clique em **Iniciar → pendrive**.
5. Na janela de revisão do lote:
   - Confira ou ajuste a ordem dos jogos (**Subir** / **Descer**).
   - Confira o título ASCII (até 32 caracteres) e a mídia (**CD** ou **DVD**). Se preferir, marque **Escolher CD/DVD automaticamente** para usar a estimativa por tamanho quando a ISO não estiver em uma pasta `CD`/`DVD`.
6. Clique em **Iniciar lote** e aguarde a mensagem de conclusão após a etapa **Sincronizando…** antes de remover o pendrive.

### Fluxo manual (para qualquer pasta no PC ou biblioteca)
1. Na aba **Imagens ISO**, selecione um ou vários jogos.
2. Clique com o botão direito do mouse e escolha **Converter para UL…** (ou pressione `Ctrl+Enter`).
3. Confirme o título, a mídia (`CD` ou `DVD`) e escolha a pasta de destino.

---

## 4. Importar Arquivos Compactados (ZIP e 7z)

### Importação manual de arquivos ZIP/7z
1. Na aba **Imagens ISO**, clique em **Importar ZIP/7z…**.
2. Escolha um ou mais arquivos compactados. O programa identifica o formato pela assinatura do arquivo, mesmo que a extensão esteja diferente.
3. Se o arquivo `.7z` for protegido por senha, informe a senha quando solicitado.
4. Na janela de prévia, confira os arquivos encontrados: imagens `.iso` e pares `.bin`/`.cue` válidos já vêm marcados (outros arquivos dentro do compactado são ignorados).
5. Confirme a extração: os arquivos são extraídos para o cache local (`~/.cache/OPLDeck/imports`), pares BIN/CUE são convertidos automaticamente para ISO e os jogos entram na aba **Imagens ISO** prontos para transferência.

### Pasta de importação automática
1. Acesse **Configurações → Pasta de import…**.
2. Clique em **Escolher pasta…** e selecione uma pasta dedicada no seu computador.
3. Marque **Monitorar a Pasta de import automaticamente** se desejar que o OPL Deck verifique novos arquivos estáveis a cada 5 segundos (ou use o botão **Buscar imports** na aba **Imagens ISO**).
4. Escolha o comportamento em **Apagar arquivos extraídos após transferência verificada**:
   - **Perguntar antes de apagar** (padrão)
   - **Sempre apagar após verificar**
   - **Nunca apagar automaticamente**
   > **Importante:** Os arquivos `.zip` e `.7z` originais nunca são apagados pelo aplicativo; apenas os temporários extraídos no cache são limpos após confirmação de que todas as partes transferidas conferem em tamanho e hash SHA-256.

---

## 5. Converter Imagens BIN/CUE para ISO

Jogos de PlayStation 2 originalmente em CD costumam vir em formato `.bin`/`.cue` (setores brutos de 2352 bytes). Para convertê-los para `.iso` (setores de 2048 bytes):

1. Abra a aba **Imagens BIN/CUE** e clique em **Adicionar BIN/CUE…** (`Ctrl+O`).
2. Prefira selecionar o arquivo `.cue` quando disponível (ou o `.bin` diretamente para imagem de faixa única).
3. Selecione um ou vários jogos identificados na tabela e clique em **Converter seleção para ISO…** (`Ctrl+Enter`).
4. Escolha o arquivo `.iso` de saída (ou a pasta de destino, no caso de vários jogos).
5. Ao terminar, a nova ISO é adicionada automaticamente à aba **Imagens ISO**, de onde pode ser transferida para o pendrive.

---

## 6. Gerenciar Jogos UL no Pendrive ou Biblioteca

Na aba **Jogos UL**, os jogos do pendrive conectado são carregados automaticamente, ou você pode clicar em **Abrir ul.cfg…** para abrir uma biblioteca em qualquer pasta:

- **Exportar seleção para ISO…**: reconstrói um arquivo `.iso` completo a partir das partes `ul.<CRC>.<ID>.<parte>` de um ou vários jogos íntegros.
- **Renomear seleção…** (`F2`): altera o título ASCII (1 a 32 caracteres) de um jogo UL, atualizando automaticamente o CRC, os nomes dos arquivos de partes e o registro em `ul.cfg`.
- **Excluir seleção…** (`Delete`): remove um ou vários jogos UL selecionados após exibir a confirmação detalhada das partes que serão apagadas e do espaço liberado. Capas na pasta `ART` e outros jogos da biblioteca são preservados.

---

## 7. Recuperar Operações e Reconstruir `ul.cfg`

### Resolver operação pendente
Se uma renomeação ou exclusão UL foi interrompida por queda de energia ou fechamento forçado, a biblioteca exibirá o aviso de **Operação pendente**:
1. Na aba **Jogos UL**, clique em **Resolver operação pendente…**.
2. Selecione a pasta raiz da biblioteca.
3. O aplicativo usará o diário `.opldeck-transaction` para restaurar as partes ao estado anterior (se o `ul.cfg` ainda não havia sido publicado) ou concluir a limpeza (se o novo índice já estava publicado).

### Reconstruir `ul.cfg` perdido ou corrompido
Se o arquivo `ul.cfg` foi apagado ou corrompido, mas os arquivos de partes (`ul.*`) continuam na pasta:
1. Na aba **Jogos UL**, clique em **Reconstruir ul.cfg…** e escolha a pasta da biblioteca.
2. O OPL Deck analisará as partes e lerá o `SYSTEM.CNF` dentro delas para identificar o ID de cada jogo.
3. Na janela de revisão, confira ou preencha o **Título** (que deve corresponder ao CRC presente no nome das partes) e confirme a mídia (**CD** ou **DVD**).
4. Clique em confirmar para gravar o novo `ul.cfg` (se já existir um `ul.cfg`, um backup `ul.cfg.backup-<id>` será criado antes da substituição).

---

## 8. Capas Locais, Busca Online e Salvamento em Lote

### Configurar busca online de capas
1. Abra **Configurações → Provedores…**.
2. Marque **Ativar busca online de capas** e mantenha selecionado o provedor padrão **xlenore / PS2 Covers** (ou cadastre fontes adicionais HTTPS usando `{id}` ou `{serial}` em **Nova fonte…**).
3. Clique em **Salvar e fechar**.

### Gerenciar capa de um único jogo (Ficha do jogo)
1. Dê duplo clique em um jogo (ou pressione `Enter`) nas abas **Imagens ISO**, **Jogos UL** ou **Imagens BIN/CUE**.
2. Na aba **Capa e biblioteca** da ficha:
   - Clique em **Escolher capa…** para usar uma imagem `.jpg`/`.png` do computador, ou
   - Clique em **Buscar capa online** para baixar uma prévia pelo ID do jogo.
3. Confira a prévia e clique em **Salvar alterações** no rodapé. A capa será gravada como PNG padronizado (até 512 px) em `ART/<ID>_COV.png`.

### Salvar capas de vários jogos em lote
1. Selecione os jogos desejados na aba **Imagens ISO** ou **Jogos UL**.
2. Acesse o menu **Biblioteca → Salvar capas da seleção em lote…** (ou clique com o botão direito e escolha **Salvar capas da seleção em lote…**).
3. Aguarde a preparação das prévias, escolha se deseja manter **Preservar capas existentes** marcado e clique em **Salvar selecionadas**.

---

## 9. Detectar Jogos Duplicados

Para verificar se o mesmo jogo (mesmo ID, como `SLUS_123.45`) aparece mais de uma vez entre suas listas **Imagens ISO** e **Jogos UL**:

1. Clique no menu **Biblioteca → Detectar jogos duplicados…**.
2. A janela listará todos os IDs repetidos, informando o título, o formato (`ISO` ou `UL`), a posição no `ul.cfg` e o caminho completo de cada arquivo.
3. Nenhum arquivo é apagado automaticamente; use a lista para decidir quais cópias manter.

---

## 10. Fila de Transferências, Histórico e Log

- **Fila de transferências** (aba): mostra todos os jogos do lote atual com estado (*Pendente*, *Preparando*, *Transferindo*, *Sincronizando*, *Concluído*, *Cancelado* ou *Falhou*). Se uma transferência for interrompida, clique em **Tentar novamente os não concluídos** para transferir apenas os jogos restantes.
- **Histórico** (menu superior ao lado de *Configurações*): abre a janela **Histórico de transferências**, listando data/hora, ID, título, operação, origem e destino de todos os jogos já transferidos, com opção de **Limpar histórico**.
- **Log** (aba): exibe até as últimas 10.000 linhas de eventos técnicos da sessão, destacando avisos em amarelo e erros em vermelho. Oferece os botões:
  - **Salvar log…**: exporta o log em um novo arquivo `.log` ou `.txt`.
  - **Limpar log**: limpa a visualização atual.
  - **Copiar Log**: copia todo o texto do log para a área de transferência.

---

## 11. Personalizar Aparência e Temas

1. Acesse **Configurações → Aparência…**.
2. Escolha entre os 8 temas disponíveis: **Sistema**, **Claro**, **Escuro**, **Meia-noite**, **Floresta**, **Ametista**, **Areia** ou **Alto contraste**.
3. Opcionalmente, marque **Usar cor personalizada nos destaques** para escolher uma cor de realce, ajuste a **Escala da fonte** (80% a 150%) e a **Densidade das listas** (*Compacta*, *Padrão* ou *Espaçosa*).
4. As alterações são pré-visualizadas imediatamente. Clique em **Aplicar** ou **Salvar e fechar** para gravar sua preferência (ou **Cancelar** para desfazer a prévia).

---

## 12. Atalhos de Teclado

| Atalho | Ação |
|---|---|
| `Ctrl+O` | Adicionar arquivos ISO / BIN/CUE ou abrir `ul.cfg` na aba atual |
| `Ctrl+F` | Focar no campo de busca da aba atual |
| `F5` | Atualizar a lista da aba atual preservando a seleção |
| `Ctrl+Enter` | Converter/exportar os jogos selecionados |
| `Ctrl+A` | Selecionar todas as linhas da tabela em foco |
| `Enter` | Abrir a ficha detalhada do jogo selecionado |
| `F2` | Renomear o jogo UL selecionado |
| `Delete` | Em ISO/BIN: remover da lista (mantém o arquivo no disco); em UL: excluir do disco com confirmação |
| `Ctrl+Shift+C` | Copiar o ID do jogo selecionado |
| `Ctrl+Shift+P` | Copiar o caminho do jogo selecionado |
| `Ctrl+Shift+O` | Abrir a pasta do jogo no gerenciador de arquivos |

---

## 13. Créditos e Licença

- **Autor:** Petrus (`@petru-sdev`) — [https://github.com/petru-sdev/OPLDeck](https://github.com/petru-sdev/OPLDeck)
- **Inspiração:** Projeto [OPLUtil](https://github.com/IsseiYoshida/OPLUtil), desenvolvido por Issei Yoshida.
- **Provedor padrão de capas online:** [xlenore / ps2-covers](https://github.com/xlenore/ps2-covers).
- **Licença:** Distribuído sob a licença **GNU General Public License v3.0 (GPLv3)** — consulte [`LICENSE`](../LICENSE) e [`NOTICE.md`](../NOTICE.md).
