# Referência de Uso — OPL Deck

Documentação técnica e referência completa de funcionamento do **OPL Deck** (`0.8.0`). Para instruções passo a passo de uso na interface gráfica, consulte o [Manual do Usuário](MANUAL.md) ou volte ao [README principal](../README.md).

---

## Sumário

1. [Estrutura do Projeto](#1-estrutura-do-projeto)
2. [Referência da Linha de Comando (CLI)](#2-referência-da-linha-de-comando-cli)
3. [Formatos, Validações e Limites Técnicos](#3-formatos-validações-e-limites-técnicos)
4. [Consistência, Locks e Recuperação de Transações](#4-consistência-locks-e-recuperação-de-transações)
5. [Capas, Provedores e Cache](#5-capas-provedores-e-cache)
6. [Arquivos de Configuração e Diretórios no Sistema](#6-arquivos-de-configuração-e-diretórios-no-sistema)
7. [Testes, Benchmarks e Empacotamento](#7-testes-benchmarks-e-empacotamento)
8. [Créditos e Licença](#8-créditos-e-licença)

---

## 1. Estrutura do Projeto

O código é dividido entre núcleo independente da interface gráfica e camada Qt/PySide6:

- **`opldeck/`** — Pacote principal Python:
  - [`opldeck/__init__.py`](../opldeck/__init__.py), [`opldeck/__main__.py`](../opldeck/__main__.py), [`opldeck/branding.py`](../opldeck/branding.py): versão (`0.8.0`), ponto de entrada CLI/GUI e metadados do projeto.
  - **`opldeck/core/`**: formatos e conversões (`formats.py`, `crc.py`, `iso.py`, `ul.py`, `binimage.py`, `conversion.py`, `batch.py`, `duration.py`, `transfer_rate.py`).
  - **`opldeck/library/`**: gerenciamento de biblioteca, dispositivos, arquivos compactados e pasta de importação (`fileio.py`, `devices.py`, `management.py`, `deletion.py`, `recovery.py`, `duplicates.py`, `diagnostics.py`, `archives.py`, `archive_7z.py`, `import_folder.py`, `import_monitor.py`).
  - **`opldeck/covers/`**: processamento de imagens de capa, provedores HTTPS, cache de pré-carregamento e gravação em lote (`artwork.py`, `providers.py`, `cover_network.py`, `cover_preload.py`, `cover_fallback.py`, `cover_batch.py`).
  - **`opldeck/ui/`**: janelas, abas, diálogos, histórico, temas e persistência de preferências (`gui.py`, `about_gui.py`, `activity_gui.py`, `archive_gui.py`, `batch_gui.py`, `cover_batch_gui.py`, `duplicates_gui.py`, `game_gui.py`, `history_gui.py`, `preferences.py`, `progress_gui.py`, `provider_gui.py`, `queue_gui.py`, `recovery_gui.py`, `settings_gui.py`, `themes.py`).
- **`tests/`** — Suíte automatizada dividida em `tests/core/`, `tests/library/`, `tests/covers/` e `tests/ui/`, além do gerador de imagens sintéticas [`tests/fixtures.py`](../tests/fixtures.py).
- **`tools/`** — Utilitários divididos em `tools/packaging/` (AppImage e PyInstaller), `tools/benchmarks/` (medição de desempenho) e `tools/reference/` (implementação independente do CRC em C).
- **`scripts/hooks/`** — Hook [`pre-commit`](../scripts/hooks/pre-commit) para prevenção de commits acidentais de binários de jogos, arquivos acima de 5 MiB ou caminhos pessoais.

---

## 2. Referência da Linha de Comando (CLI)

Todos os subcomandos de terminal funcionam **sem carregar o Qt/PySide6** e retornam dados estruturados em JSON no `stdout` (exceto `crc`, que imprime o hexadecimal de 8 dígitos). O progresso das operações é emitido no `stderr`.

### Sintaxe geral

```bash
python3 -m opldeck <subcomando> [argumentos]
# ou, quando instalado no ambiente virtual:
opldeck <subcomando> [argumentos]
```

### Subcomandos disponíveis

| Subcomando | Uso | Descrição |
|---|---|---|
| `gui` *(ou sem subcomando)* | `python3 -m opldeck [gui]` | Abre a interface gráfica Qt/PySide6. |
| `inspect` | `python3 -m opldeck inspect <caminho.iso>` | Lê o Volume Descriptor ISO9660 e o `SYSTEM.CNF`, retornando ID, título sugerido, tamanho, mídia, partes UL e eventuais avisos. |
| `list-ul` | `python3 -m opldeck list-ul <caminho/ul.cfg>` | Lista todos os registros de 64 bytes contidos em um arquivo `ul.cfg` (`title`, `game_id`, `parts`, `media`, `crc`). |
| `crc` | `python3 -m opldeck crc "<titulo>"` | Calcula o CRC32 USBExtreme em hexadecimal maiúsculo (8 dígitos) para um título ASCII de 1 a 32 caracteres. |
| `install` | `python3 -m opldeck install <origem.iso> <destino> [--title "<titulo>"] [--media CD\|DVD]` | Divide uma ISO em partes UL de 1 GiB no diretório `<destino>` e atualiza `ul.cfg` atomicamente. Fora de pastas `CD/` ou `DVD/`, `--media` é obrigatório. |
| `export` | `python3 -m opldeck export <caminho/ul.cfg> <GAME_ID> <saida.iso>` | Reconstrói uma imagem `.iso` a partir das partes UL do `<GAME_ID>` registrado em `ul.cfg`. |
| `inspect-bin` | `python3 -m opldeck inspect-bin <caminho.bin\|caminho.cue>` | Identifica uma imagem PS2 em BIN raw de faixa única ou CUE, informando ID, modo de setor, contagem de setores e tamanhos raw/ISO. |
| `bin2iso` | `python3 -m opldeck bin2iso <caminho.bin\|caminho.cue> <saida.iso>` | Converte BIN/CUE de PS2 (Mode 1 ou Mode 2 Form 1) para um novo arquivo `.iso`, validando o cabeçalho de todos os setores. |
| `rename-ul` | `python3 -m opldeck rename-ul <caminho/ul.cfg> <GAME_ID> "<novo_titulo>"` | Atualiza o título ASCII, recalcula o CRC, renomeia as partes UL e publica o novo `ul.cfg` com diário transacional. |
| `delete-ul` | `python3 -m opldeck delete-ul <caminho/ul.cfg> <GAME_ID> --yes` | Remove permanentemente as partes UL do jogo e seu registro em `ul.cfg`. A flag `--yes` é obrigatória. |
| `recover-ul` | `python3 -m opldeck recover-ul <diretorio_biblioteca>` | Resolve uma operação de gerenciamento UL interrompida (`.opldeck-transaction`), revertendo ou concluindo conforme o estado do índice. |
| `scan-ul` | `python3 -m opldeck scan-ul <diretorio_biblioteca> [--output <plano.json>]` | Analisa partes `ul.*` na biblioteca e gera um plano JSON editável para reconstrução de `ul.cfg`. |
| `rebuild-ul` | `python3 -m opldeck rebuild-ul <diretorio_biblioteca> --plan <plano.json> --yes [--replace]` | Reconstrói `ul.cfg` a partir de um plano revisado. Se `ul.cfg` já existir, exige `--replace` e cria backup automático. |

### Códigos de saída (Exit Codes)
- `0`: operação concluída com êxito.
- `1`: erro de formato (`FormatError`), erro de entrada/saída (`OSError`) ou cancelamento (`CancelledError`).
- `2`: erro de argumentos de linha de comando (por exemplo, omissão de `--yes` em `delete-ul` ou `rebuild-ul`).
- `130`: interrupção por teclado (`Ctrl+C` / `KeyboardInterrupt`), informando no `stderr` se ficou alguma pendência de recuperação.

---

## 3. Formatos, Validações e Limites Técnicos

### ISO9660 (`opldeck/core/iso.py`)
- Exige setores lógicos de **2048 bytes**, Primary Volume Descriptor válido (`CD001` no setor 16) e arquivo `SYSTEM.CNF` na raiz com linha `BOOT2` contendo um serial válido no formato `AAAA_999.99` (ex.: `SLUS_123.45`, `SCES_508.85`).
- **Inferência de mídia**:
  - Se o arquivo está dentro de uma pasta chamada `CD` ou `DVD`, a pasta define a mídia.
  - Fora dessas pastas, a interface pode estimar experimentalmente **CD** para tamanhos menores que `734.068.736` bytes (~700 MiB) e **DVD** a partir desse valor, sinalizando como estimativa.
- **Diagnósticos de alinhamento OPL (`opldeck/library/diagnostics.py`)**: emite avisos quando o nome do arquivo excede 160 bytes UTF-8, diverge do ID interno do `SYSTEM.CNF`, contém caracteres de controle ou está na pasta errada (`CD/` vs `DVD/`), sem renomear ou mover o arquivo original.

### Formato UL / USBExtreme (`opldeck/core/ul.py` e `opldeck/core/conversion.py`)
- Cada registro em `ul.cfg` possui exatamente **64 bytes**:
  - `0x00..0x1F` (32 bytes): título ASCII imprimível preenchido com zeros (`\x00`).
  - `0x20..0x2E` (15 bytes): prefixo `ul.` + ID do jogo (ex.: `ul.SLUS_123.45`) preenchido com zeros.
  - `0x2F` (1 byte): quantidade de partes (`1` a `255`).
  - `0x30` (1 byte): tipo de mídia (`0x12` para CD, `0x14` para DVD).
  - `0x31..0x3F` (15 bytes): bytes reservados (`0x08` no byte `0x34` nas gravações novas; registros existentes em `ul.cfg` são preservados byte a byte durante novas instalações).
- Cada parte UL tem tamanho máximo de **1 GiB** (`1.073.741.824` bytes = `PART_SIZE`) e nome `ul.<CRC32_HEX>.<GAME_ID>.<indice_hex_2_digitos>` (ex.: `ul.84E3C92A.SLUS_123.45.00`).
- O tamanho máximo aceito para leitura de `ul.cfg` é **16 MiB** (`MAX_CFG_SIZE`).

### Imagens BIN/CUE (`opldeck/core/binimage.py`)
- Suporta imagens raw de faixa única de PS2 com setores de **2352 bytes**:
  - **Mode 1** (`TRACK 01 MODE1/2352`): 16 bytes de cabeçalho + 2048 bytes de payload.
  - **Mode 2 Form 1** (`TRACK 01 MODE2/2352`): 24 bytes de cabeçalho/subcabeçalho XA + 2048 bytes de payload.
- Recusa múltiplas faixas (`TRACK 02`), faixas de áudio, setores Form 2, pregaps complexos ou caminhos de `.bin` que apontem para fora do diretório do `.cue`.
- Durante a conversão `BIN → ISO`, o sincronismo, o modo e o subcabeçalho XA são validados em **todos os setores** da imagem; bytes de paridade EDC/ECC não são recalculados.

### Arquivos Compactados ZIP e 7z (`opldeck/library/archives.py` e `opldeck/library/archive_7z.py`)
- O formato é detectado pela assinatura binária (magic bytes `PK\x03\x04` / `PK\x05\x06` para ZIP e `7z\xbc\xaf\x27\x1c` para 7z).
- **Limites de proteção contra abusos (`ArchiveLimits`)**:
  - Tamanho total descompactado por operação: **32 GiB**.
  - Tamanho máximo por membro: **16 GiB**.
  - Quantidade máxima de entradas: **10.000**.
  - Razão máxima de compressão: **2000:1**.
  - Metadados de leitura ZIP: limitados a **32 MiB** por bloco antes de alocar o diretório central.
- Bloqueia caminhos absolutos, travessia `..`, links simbólicos e arquivos especiais dentro dos pacotes compactados.
- Para `.7z`, exige `py7zr >= 1.1.4, < 1.2` (versões anteriores são recusadas antes de abrir o arquivo). Na extração sem senha, prioriza o binário `7zz`/`7z` do sistema se disponível, usando `py7zr` como fallback automático e para arquivos criptografados com senha.

---

## 4. Consistência, Locks e Recuperação de Transações

- **Nunca sobrescreve destinos**: ISOs exportadas e partes UL são criadas em modo exclusivo (`xb`). Se o arquivo de saída já existir, a operação aborta sem tocá-lo.
- **Publicação atômica**: em instalações e reconstruções, o novo índice é gravado primeiro em um temporário (`.opldeck-cfg-*` ou `.opldeck-rebuild-*`), sincronizado com `os.fsync` no arquivo e no diretório, e publicado com `os.replace` somente após conferir que o `ul.cfg` original não foi modificado por outro processo durante a cópia.
- **Lock cooperativo (`directory_lock`)**: utiliza `fcntl.flock(LOCK_EX | LOCK_NB)` no diretório da biblioteca para impedir operações simultâneas entre instâncias do OPL Deck.
- **Diário transacional (`.opldeck-transaction`)**:
  - Renomeações e exclusões criam o diretório `.opldeck-transaction` com `manifest.json` e `next.cfg` antes de mover qualquer parte via `renameat2(RENAME_NOREPLACE)`.
  - Na exclusão, as partes são movidas para dentro de `.opldeck-transaction` antes de publicar o novo `ul.cfg`, permitindo restauração integral caso o processo seja interrompido antes da publicação do índice.

---

## 5. Capas, Provedores e Cache

- **Formato de entrada**: JPEG ou PNG regular (sem seguir links simbólicos), de até **20 MiB** e dimensão máxima de **4096 × 4096 pixels**.
- **Formato de saída**: PNG verdadeiro (`RGB` ou `RGBA` de 8 bits), redimensionado proporcionalmente para no máximo **512 pixels** no maior lado (imagens menores não são ampliadas), salvo atomicamente em `ART/<GAME_ID>_COV.png`.
- **Reconhecimento de capas legadas**: lê também pastas `art` (minúsculo), extensões `.jpg`/`.jpeg`/`.png`, nomes com hífen (`SLUS-12345_COV.png`) ou pelo título, preparando a conversão para o nome PNG canônico sem apagar o arquivo legado original.
- **Provedores online (`opldeck/covers/providers.py`)**:
  - Provedor embutido: `xlenore / PS2 Covers` (`https://raw.githubusercontent.com/xlenore/ps2-covers/main/covers/default/{serial}.jpg`).
  - Suporta até **32 provedores personalizados** com URL `https://` contendo `{id}` (`SLUS_123.45`) ou `{serial}` (`SLUS-12345`).
  - Timeout por requisição: **30 segundos**.
- **Cache de pré-carregamento (`opldeck/covers/cover_preload.py`)**: mantido apenas em memória durante a sessão, limitado a **64 MiB** ou **256 capas** (política LRU). Pausa automaticamente enquanto uma conversão ou transferência está em andamento.

---

## 6. Arquivos de Configuração e Diretórios no Sistema

| Finalidade | Caminho padrão no Linux | Observações |
|---|---|---|
| **Configurações (INI)** | `$XDG_CONFIG_HOME/OPLDeck/OPLDeck.ini` *(normalmente `~/.config/OPLDeck/OPLDeck.ini`)* | Armazena tema (`appearance/*`), provedores (`providers/*`), pasta de importação (`import/*`) e histórico de transferências (`history/transfers`, até 1.000 registros). |
| **Cache de importação ZIP/7z** | `$XDG_CACHE_HOME/OPLDeck/imports` *(normalmente `~/.cache/OPLDeck/imports`)* | Pode ser sobrescrito pela variável de ambiente `OPLDECK_IMPORT_CACHE`. Criado com permissão `0700`. |
| **Transação UL na biblioteca** | `<raiz_biblioteca>/.opldeck-transaction` | Criado apenas durante renomeação/exclusão UL e removido automaticamente ao concluir. |

---

## 7. Testes, Benchmarks e Empacotamento

### Executar a suíte de testes

```bash
python3 -m unittest discover -v
```

Para executar também os testes opcionais de conversão e arquivos ZIP64 multi-GiB (requer ~10 GiB livres em disco):

```bash
OPLDECK_LARGE_TESTS=1 python3 -m unittest tests.core.test_conversion.LargeConversionTests -v
OPLDECK_LARGE_TESTS=1 python3 -m unittest tests.library.test_archives.LargeArchiveTests -v
```

### Executar benchmarks

```bash
QT_QPA_PLATFORM=offscreen python3 -m tools.benchmarks.benchmark_gui --rows 2000 --repeat 3
QT_QPA_PLATFORM=offscreen python3 -m tools.benchmarks.benchmark_cover_batch --rows 1000 --repeat 3
```

### Autotestes de empacotamento e construção de AppImage

```bash
python3 tools/packaging/packaging_entry.py --self-test-gui
python3 tools/packaging/packaging_entry.py --self-test-archives
python3 tools/packaging/build_appimage.py --appdir-only
```

---

## 8. Créditos e Licença

- **Desenvolvido por:** Petrus (`@petru-sdev`) — [https://github.com/petru-sdev/OPLDeck](https://github.com/petru-sdev/OPLDeck)
- **Inspiração:** [OPLUtil](https://github.com/IsseiYoshida/OPLUtil), de Issei Yoshida.
- **Licença:** GNU General Public License v3.0 ([`LICENSE`](../LICENSE)) · Avisos legais em [`NOTICE.md`](../NOTICE.md).
