# OPL Deck

**Versão 0.8.0** · [Manual passo a passo](tutorial/MANUAL.md) · [Referência de uso](tutorial/referencia-de-uso.md) · [Novidades e limites](docs/versao-0.9.0.md) · [Histórico](CHANGELOG.md) · [Relatar problema](https://github.com/petru-sdev/opldeck/issues)

Gerenciador de imagens de PlayStation 2 para Open PS2 Loader, anteriormente chamado OPLUtil para Linux. Implementação em **Python 3.11+ e Qt/PySide6**, com interface em português e núcleo independente da GUI.

## Executar

O executável local da 0.9.0 é `dist/0.9.0/OPLDeck-0.9.0-x86_64.AppImage`, construído em Debian 12 para Linux x86_64, com requisito observado de **glibc 2.36 ou superior**. Não precisa de Python instalado separadamente. Veja [compilação, execução sem FUSE e limites](docs/compilacao-linux.md). A existência do pacote local não significa que um release já foi publicado no GitHub.

Na pasta do projeto:

```bash
python3 -m opldeck
```

O ambiente de desenvolvimento já possui PySide6. Em outro ambiente, instale em um ambiente virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
opldeck
```

O pacote instalado disponibiliza os comandos `opldeck` e `opl-deck`.

**7z exige py7zr>=1.1.4,<1.2**, com correções de segurança. Versões antigas são bloqueadas antes de analisar arquivos; ZIP continua funcionando. Na revisão de 28/10, py7zr 1.1.4 e pyppmd 1.3.1 foram instalados isoladamente e os testes de 7z passaram. A instalação pelo projeto acima resolve as dependências em um ambiente com rede. Veja [correções, testes e limites](docs/versao-0.9.0.md#seguranca-e-compactados).

## Disponível

- Importar ISOs e ler o ID em `SYSTEM.CNF` sem montar a imagem.
- Exibir título, ID, mídia, tamanho, previsão de partes UL e caminho.
- Abrir `ul.cfg`, interpretar registros de 64 bytes e diagnosticar partes ausentes, tamanhos inválidos, links, arquivos especiais e falhas de acesso sem ocultar os outros jogos.
- Calcular o CRC USBExtreme e serializar registros UL pelo núcleo Python.
- Importar em segundo plano e apresentar erros de leitura.
- Importar ZIP/ZIP64 e 7z pela assinatura, revisar o conteúdo, extrair ISO ou BIN/CUE no PC e transferir com progresso amarelo/azul.
- Cadastrar uma Pasta de import opcional e escolher Sempre/Nunca/Perguntar para limpar extraídos após transferência verificada, preservando os compactados originais.
- Escolher opcionalmente CD/DVD automático na instalação, com aviso explícito quando a escolha for uma estimativa por tamanho.
- Consultar ocupação do pendrive numa barra sem texto, com espaço livre, usado e total ao lado.
- Converter uma ISO selecionada para partes UL de 1 GiB e reconstruir uma ISO a partir de um jogo UL.
- Detectar pendrives montados, carregar jogos UL/ISO automaticamente e preencher o destino de transferência.
- Transferir vários jogos em sequência, com revisão e ajuste de ordem, progresso total e progresso do jogo atual.
- Acompanhar a aba **Fila de transferências**, cancelar e tentar novamente somente os jogos não concluídos.
- Detectar IDs duplicados entre ISOs e registros UL carregados em **Biblioteca → Detectar jogos duplicados…**, com caminhos e sem exclusão automática.
- Converter BIN raw de PS2 (Mode 1 ou Mode 2 Form 1) e CUE de faixa única para ISO, validando a estrutura dos setores.
- Confirmar título ASCII e mídia CD/DVD antes de instalar; preservar registros anteriores de `ul.cfg` byte a byte.
- Acompanhar bytes, partes, velocidade dos últimos 30 segundos em MiB/s, tempo decorrido e restante estimado em segundos/minutos/horas/dias; cancelar a conversão.
- Consultar CRC, tamanho exato, origem dos metadados e offset do registro UL nas tabelas e no painel de detalhes.
- Consultar a aba **Log**, com horário, nível, caminhos, etapas, erros e cancelamentos; salvar o log em texto UTF-8.
- Renomear jogos UL, atualizando título, CRC e nomes das partes; excluir um ou vários jogos com confirmação única.
- Resolver operações de gerenciamento interrompidas usando o diário local, com restauração antes da publicação do índice e conclusão após a publicação.
- Reconstruir `ul.cfg` perdido ou corrompido a partir das partes, com prévia, conferência de ID/CRC, confirmação de mídia e backup do índice existente.
- Abrir a ficha de um jogo, consultar seus dados e selecionar uma capa local JPEG/PNG para a pasta `ART`.
- Ativar o provedor **xlenore / PS2 Covers** em **Configurações → Provedores** e buscar uma capa pelo ID na ficha, com prévia antes de salvar.
- Cadastrar, editar e remover fontes de capas por endereço HTTPS; consultar primeiro somente a fonte selecionada e oferecer alternativas quando ela não encontrar a capa.
- Salvar capas da seleção em lote, com prévias, preservação de capas existentes, confirmação de substituições e resultados por jogo.
- Pré-carregar capas silenciosamente após reconhecer o pendrive: ler capas locais em segundo plano e preparar as ausentes online quando o provedor estiver habilitado.
- Personalizar **Configurações → Aparência**: oito temas, cor de destaque, texto de 80% a 150% e listas compactas/padrão/espaçosas, com prévia reversível e preferências salvas.
- Consultar **Downloads**, primeira aba, à esquerda de **Imagens ISO**, com **Download de jogos — Em breve**. A área é somente visual; busca e download ainda não estão disponíveis.
- Identificar os resultados por cores e ícones: verde para verificações sem falhas, amarelo para avisos/pendências e vermelho para falhas. Avisos e erros também recebem cores na aba Log.

A origem é preservada nas conversões. ISO → UL, UL → ISO e BIN/CUE → ISO aceitam lotes sequenciais; a exclusão UL também aceita seleção múltipla; renomeação continua individual. A reconstrução do índice reúne os grupos escolhidos na prévia. Importação de nomes, integração com APIs de metadados, outros tipos de arte e formatação ainda não estão implementados. Veja o [roadmap](docs/versao-0.9.0.md#proximas-etapas).

Na aba **Imagens ISO**, adicione seus arquivos e conecte o pendrive. O campo **Pendrive** mostra o destino e um ícone animado durante a leitura. O programa carrega `ul.cfg` em **Jogos UL** e ISOs da raiz/`CD`/`DVD` em **Imagens ISO**, preservando sua lista local. **Iniciar → pendrive** é liberado depois da leitura, com destino gravável e ISOs locais identificadas. A revisão já vem com o destino preenchido; confirme título e mídia antes de transferir. Sem linhas selecionadas, usa todas as ISOs locais visíveis. Arquivos já no próprio pendrive não entram nessa transferência.

Para arquivos compactados, use **Importar ZIP/7z…**. A assinatura identifica o formato mesmo com outra extensão. Revise a lista: ISOs e pares BIN/CUE ficam selecionados; os demais arquivos são ignorados. A extração usa uma pasta exclusiva no PC, normalmente `~/.cache/OPLDeck/imports`. O programa identifica SYSTEM.CNF e converte BIN/CUE para ISO local antes de adicionar à lista. Depois, selecione os jogos e inicie a transferência.

ZIP/ZIP64 usa a biblioteca padrão do Python; 7z prefere `7zz`/`7z` do sistema e usa **py7zr** quando ausente. py7zr também faz a listagem de metadados e processa arquivos protegidos. A senha é pedida em campo mascarado, sem gravação ou log, com mensagem específica se estiver incorreta. ZIP AES, RAR, TAR e arquivos divididos (`.7z.001`, `.z01`) não são suportados nesta etapa. Veja [dependências e AppImage](docs/dependencias-e-appimage.md).

Em **Configurações → Importação**, use **Pasta de import…** para cadastrar uma pasta dedicada, por exemplo em Downloads, e ative a leitura automática se desejar. Mova para ela apenas arquivos concluídos. O programa verifica os arquivos estáveis, prepara os jogos e aguarda você iniciar a transferência. Na aba **Imagens ISO**, **Buscar imports**, à direita de **Importar ZIP/7z…**, relê imediatamente a pasta cadastrada. O botão fica bloqueado com a função desativada, sem ZIP/7z pendentes ou durante outra operação. A disponibilidade acompanha a verificação em segundo plano, a cada cinco segundos; a busca mantém a espera por arquivos estáveis. A opção **Apagar arquivos extraídos após transferência verificada** oferece **Sempre**, **Nunca** e **Perguntar**. A limpeza exige que todos os jogos da origem tenham sido transferidos e conferidos por tamanho e SHA-256; falhas preservam os extraídos para revisão. **Os ZIP/7z originais são sempre preservados.**

A extração recusa caminhos perigosos, links, dispositivos, duplicatas e sobrescritas; limita o total a 32 GiB, 10.000 entradas e razão de compressão 2000:1, além de conferir espaço livre. A abertura de ZIP também limita cada leitura de metadados a 32 MiB, antes de analisar o diretório central. Cancelamento limpa os parciais da importação atual, e um compactado com erro não impede os demais do lote. Registros inválidos de importação impedem retomada/limpeza e preservam os arquivos para revisão. Detalhes e limites em [importação e transferências](docs/versao-0.9.0.md#seguranca-e-compactados) e na [revisão de segurança](docs/versao-0.9.0.md#seguranca-e-compactados).

A barra principal permanece **azul**, reservando metade do trabalho para extração e metade para transferência das mesmas ISOs; a barra do arquivo atual fica **amarela na extração** e azul na transferência. Ao terminar de extrair, a atividade indica que aguarda transferência. Revise os jogos e clique em Iniciar: o total continua do trabalho já feito. O total considera os jogos escolhidos; alterar a seleção recalcula a base. Esse acompanhamento é mantido durante a sessão, sem persistência de fila ao fechar o programa.

Na instalação individual ou em lote, marque **Escolher CD/DVD automaticamente** para dispensar a escolha de cada mídia. A pasta `CD`/`DVD` tem prioridade; fora dela, a regra experimental é **CD abaixo de 734.068.736 bytes e DVD a partir desse valor**. A direção dessa comparação ainda é uma hipótese não validada no binário original. O diálogo avisa que é estimativa, e desmarcar restaura a escolha manual. A opção começa desmarcada em cada revisão.

A barra de ocupação ao lado do armazenamento não contém números; o texto mostra **livre, usado e total**. Os valores são lidos ao reconhecer o pendrive, após operações e periodicamente quando a fila está livre. Ao desconectar/trocar o dispositivo, os dados antigos são limpos. Veja os [requisitos de execução](docs/versao-0.9.0.md#requisitos-e-execucao).

Um único pendrive montado é selecionado automaticamente; com vários, escolha o destino. **Reler pendrive** atualiza a leitura. Volumes somente leitura podem ser consultados; remoção bloqueia o início e solicita cancelamento da transferência ativa. A descoberta também funciona quando o dispositivo já está conectado ao iniciar. Depende de `lsblk` (util-linux) e da montagem pelo Linux; se necessário, abra o dispositivo no gerenciador de arquivos.

Para uma biblioteca escolhida manualmente, use **Converter para UL…** no menu de contexto ou `Ctrl+Enter`. Na aba **Jogos UL**, abra `ul.cfg`, selecione um jogo e use **Exportar seleção para ISO…**, escolhendo um arquivo novo. O destino deve existir como diretório; arquivos existentes nunca são substituídos pela conversão.

Na aba **Imagens BIN/CUE**, adicione o BIN ou o CUE, selecione o jogo e use **Converter seleção para ISO…**. A tabela mostra setores, formato, tamanhos raw/ISO e offset dos dados úteis. A ISO concluída entra automaticamente na aba Imagens ISO, onde pode ser convertida para UL em uma segunda operação.

Para **transferir vários jogos**, selecione linhas usando **Ctrl** ou **Shift** (ou `Ctrl+A` com foco na tabela) e use **Iniciar → pendrive**, o botão de exportação/conversão das outras abas ou `Ctrl+Enter` para o fluxo manual. A revisão mostra os jogos na ordem da tabela e um único destino: o pendrive reconhecido no fluxo automático ou uma pasta escolhida no fluxo manual. Em ISO → UL, revise os títulos e confirme CD/DVD quando a mídia for estimada. Em exportação UL e conversão BIN/CUE, cada saída será `<ID>.iso`.

Durante o lote aparecem **duas barras**: **Total do lote**, proporcional aos bytes de todos os jogos, e **Jogo atual**, reiniciada para cada jogo. O texto mostra, por exemplo, **Jogo 2 de 5**, título e etapa. O programa confere origens, destinos existentes, IDs/títulos repetidos e espaço para o lote antes de começar, e as conversões mantêm suas próprias validações. A estimativa usa a velocidade dos últimos 30 segundos e avisa quedas de ritmo. A etapa **Sincronizando…** executa flush/fsync antes da conclusão; aguarde a confirmação antes de remover o dispositivo.

**Cancelar lote** interrompe o jogo em andamento e impede o início dos próximos. Os jogos já concluídos são mantidos; os parciais do jogo atual são tratados pela rotina de conversão. Falhas também interrompem a fila, com resumo e diagnóstico no Log. Na aba **Fila de transferências**, **Tentar novamente os não concluídos** reinicia somente os jogos que não terminaram, revalidando as condições do destino. A fila não é persistida nem retomada após fechar o programa; consulte os limites de consistência abaixo. Ao terminar, a lista UL ou a lista de ISOs recebe os resultados concluídos. O lote converte/instala os arquivos do jogo; não copia capas e outros complementos.

Na aba **Jogos UL**, use **Renomear seleção…** para informar um título ASCII de até 32 caracteres. **Excluir seleção…** mostra título, ID, partes presentes/ausentes, bytes e índice antes da confirmação; os detalhes listam os nomes dos arquivos. A exclusão concluída é permanente. Jogos UL com partes ausentes, vazias ou de tamanho inválido também podem ser renomeados e excluídos. Renomear preserva essas falhas; excluir remove o registro e somente as partes referenciadas que ainda existem, preservando capas e outros jogos. A exportação permanece bloqueada quando as partes estão incompletas ou com tamanhos inválidos. O gerenciamento exige que o índice se chame `ul.cfg`.

Para **excluir vários jogos UL**, selecione com Ctrl/Shift ou Ctrl+A e use **Excluir seleção…**, o menu de contexto **Excluir N jogos selecionados…** ou Delete. A confirmação reúne títulos, IDs, partes presentes/ausentes e bytes; expanda os detalhes para conferir os arquivos. Somente linhas selecionadas e visíveis entram no lote, inclusive jogos com falhas. A exclusão é sequencial, com progresso e cancelamento. Falha ou cancelamento impede os próximos jogos; os já excluídos permanecem excluídos, e o resumo informa o total. A lista é atualizada ao terminar. Se o índice ou as partes mudarem desde a confirmação, a operação para para nova revisão.

O **painel de atividade acima das tabelas** indica leitura, transferência e gerenciamento com animação e tempo decorrido. Durante a preparação, a barra fica indeterminada; com o progresso disponível, mostra a porcentagem real. Lotes exibem o jogo atual e duas barras, uma para o total e outra para o jogo. Transferências também mostram volume transferido, velocidade e estimativa restante. Ao cancelar, aguarde a confirmação no painel. Conclusão, cancelamento ou falha permanecem visíveis mesmo após a atualização automática da lista, com ícone e texto. Para consultar caminhos, CRC, bytes e informações técnicas da operação, clique em **Mostrar detalhes técnicos**.

As tabelas ISO, UL e BIN/CUE têm linhas coloridas e uma coluna Status compacta, somente com ícones. Verde significa que as verificações disponíveis não encontraram falhas, sem garantir a integridade completa do conteúdo. Amarelo indica, por exemplo, mídia estimada, título UL que precisa de ajuste, identificação parcial de BIN ou operação pendente. Vermelho indica falha de leitura, partes ausentes ou tamanhos inválidos. Passe o mouse sobre o ícone para consultar a explicação, também disponível no painel de detalhes. As cores acompanham o tema claro/escuro.

Os contadores mostram **total de jogos**, **jogos íntegros\***, **jogos com avisos** e **jogos falhos** da aba atual. O asterisco indica o alcance básico da verificação, sem comparação por hash. Avisos não entram na contagem de íntegros. A busca por título/ID/caminho e o filtro por resultado ocultam linhas, mantendo os totais da aba; o indicador ao lado do filtro informa quantos jogos estão visíveis.

A busca aceita trechos com ou sem acentos: `acao` encontra `Ação`. Espaços repetidos são normalizados, e o mesmo ID pode ser pesquisado como `SLUS_123.45`, `SLUS-12345` ou `SLUS12345`. Os nomes e caminhos exibidos permanecem intactos. Clique no cabeçalho para ordenar tamanhos pelos bytes exatos, inclusive quando a tela mistura MiB/GiB ou arredonda dois tamanhos para o mesmo valor. Partes, setores, bytes e CRC também usam ordem numérica; valores indisponíveis ficam no fim da ordem crescente.

Na lista UL, a leitura verifica se cada parte pode ser aberta como arquivo regular, sem seguir links ou aguardar FIFOs. Problemas de acesso ficam na linha do jogo; partes ausentes e tamanhos inválidos são apresentados juntos, com o número de cada parte. Os bytes exibidos somam somente as partes cujos tamanhos puderam ser consultados. Botões de exportação UL e conversão BIN/CUE acompanham os bloqueios do menu, a seleção, os filtros e as operações em andamento. Isso continua sendo uma verificação básica, sem leitura integral ou comparação por hash.

Clique com o botão direito em um jogo para converter/exportar, abrir a ficha, abrir sua pasta ou copiar ID/caminho. Jogos UL também oferecem renomeação e exclusão com confirmação. Em ISO/BIN, **Remover da lista** mantém o arquivo no disco. Duplo clique abre a ficha. Comandos que exigem um jogo válido ou não podem ser usados durante uma operação ficam desativados no menu.

Na **Ficha do jogo**, consulte **Informações**, **Dados técnicos e diagnóstico** e **Capa e biblioteca**. A capa maior mostra dimensões/formato; **Ampliar capa** abre a imagem com rolagem. **Copiar ID** copia o identificador, e **Gerenciar capa…** abre a aba de edição de capa. **Renomear jogo…** reaproveita o gerenciamento UL; título de ISO/BIN, ID, mídia e demais campos permanecem somente para consulta nesta etapa. Os botões de conversão/exportação e abertura de pasta dão acesso às operações existentes. A ficha UL também oferece **Excluir jogo…**, com a mesma confirmação da tabela, inclusive para jogos com falhas.

Na aba **Capa e biblioteca**, use **Escolher capa…** para visualizar uma imagem local. Para gravar, use **Salvar alterações** no rodapé da ficha, ao lado de **Exportar para ISO…** (ou Converter), disponível em todas as abas. O botão fica bloqueado sem prévia pendente e após salvar ou descartar; habilita quando há uma capa pendente, biblioteca definida e edição disponível. A raiz é reconhecida pelo índice UL ou pela pasta `CD`/`DVD` de uma ISO. Para arquivos soltos e BIN/CUE, o pendrive selecionado é sugerido como **Biblioteca OPL**; sem ele, escolha explicitamente a biblioteca. A escolha vale para a ficha aberta. A pasta `ART` só é criada ao salvar. Toda gravação usa PNG verdadeiro em `ART/<ID>_COV.png`, com redução proporcional até 512 pixels por lado, sem ampliar imagens pequenas e preservando a transparência. Substituir esse PNG quando já existe pede confirmação; fechar uma prévia escolhida manualmente permite descartar ou continuar editando.

O reconhecimento aceita `ART`/`art`, extensões JPG/JPEG/PNG em maiúsculas/minúsculas, serial com hífen e capas pelo título/nome do arquivo. JPGs e nomes antigos são preparados para salvar como PNG pelo ID, preservando a origem. O PNG canônico tem prioridade quando coexiste com os legados. Várias origens por ID sem PNG canônico continuam exigindo resolver a ambiguidade antes de editar. A ficha informa a pasta procurada quando a capa está ausente ou inválida. Veja [capas preparadas e diagnósticos](docs/versao-0.9.0.md#compatibilidade-e-capas).

Depois de reconhecer o pendrive, o programa prepara as capas silenciosamente em segundo plano. Lê as existentes e, com o provedor habilitado, busca capas ausentes/ilegíveis. As imagens entram no cache já reduzidas. Ao abrir uma capa online, JPG, com nome legado ou dimensões acima do limite de saída, a prévia fica pronta para **Salvar alterações**; um PNG canônico já adequado não cria alteração pendente. A preparação não cria ART nem grava capas automaticamente. Fechar uma prévia automática não pede confirmação de descarte. A escolha manual continua protegida pela confirmação existente.

O cache dura enquanto o programa estiver aberto e tem limite de 64 MiB de imagens/256 entradas; capas antigas podem ser preparadas novamente ao abrir a ficha. A preparação pausa durante transferências e descarta resultados de um pendrive removido/substituído. O salvamento confere novamente se o arquivo existente mudou. Esses recursos estão incluídos no [fechamento da 0.8.0](docs/versao-0.9.0.md#novidades), conforme a decisão final do usuário. Os relatórios da proposta anterior 0.9.0 permanecem preservados como histórico técnico.

**Configurações → Provedores** também pode ser aberta pelo botão **Provedores…** na ficha. Marque **Ativar busca online de capas**, selecione **xlenore / PS2 Covers** e use **Aplicar** ou **Salvar e fechar**. A preferência fica salva; a busca online vem desativada por padrão. Na ficha, clique em **Buscar capa online**, confira a prévia e use **Salvar alterações** no rodapé. A consulta usa o ID exato do jogo; uma capa ausente não provoca tentativa automática com outra região. A imagem permanece em memória até salvar; substituir uma capa existente pede confirmação. **Cancelar busca** interrompe a consulta, e falhas preservam a capa e a prévia anteriores. A escolha local continua disponível.

Em **Configurações → Provedores**, use **Nova fonte…**, **Editar fonte…** e **Remover fonte** para administrar até 32 fontes adicionais. Informe um nome e o endereço HTTPS direto da imagem JPEG/PNG, com `{id}` (exemplo: `SLUS_207.18`) ou `{serial}` (`SLUS-20718`). O editor mostra um exemplo do endereço resultante. **Aplicar** ou **Salvar e fechar** confirma o cadastro; **Cancelar** descarta alterações ainda não aplicadas. A fonte xlenore permanece disponível e não pode ser removida.

A busca manual e a preparação em segundo plano consultam primeiro **somente a fonte selecionada**. Se ela responder que a capa não existe (HTTP 404), uma única alternativa cadastrada é consultada automaticamente. Havendo várias alternativas, a tela **Consultar outras fontes?** permite escolher, com apenas a primeira marcada inicialmente. As consultas seguem a ordem da lista e param na primeira imagem válida. Na preparação de fundo, a escolha vale para as capas ausentes daquela leitura do pendrive; **Agora não** mantém somente a principal até a próxima leitura. Erros de conexão, acesso, limite ou imagem inválida não provocam troca de fonte. O cadastro aceita imagens diretas; páginas de pesquisa e serviços com API/login precisam de integração própria.

O provedor inicial usa o padrão público documentado em [xlenore/ps2-covers](https://github.com/xlenore/ps2-covers), sem conta. A consulta assíncrona tem limite de 30 segundos e aceita JPEG/PNG de até 20 MiB e 4096 pixels por lado. Configurações usam as chaves `providers/enabled`, `providers/cover` e `providers/custom` no mesmo INI da aparência. Falhas de conexão ou limites do provedor suspendem a preparação automática restante; use **Reler pendrive** para reiniciá-la ou tente a busca manual. Alterar as fontes invalida consultas e escolhas antigas. Metadados continuam futuros. Para salvar várias capas, selecione jogos ISO/UL e use **Biblioteca → Salvar capas da seleção em lote…**; revise as imagens e destinos, mantenha ou desmarque **Preservar capas existentes**, e clique em **Salvar selecionadas**. A preparação só usa temporários no PC; a gravação explícita mantém as proteções contra mudanças desde a prévia. Detalhes e limites em [fila, duplicados e capas](docs/fila-duplicados-capas.md). Orientações atuais sobre [capas](docs/versao-0.9.0.md#compatibilidade-e-capas).

Em **Configurações → Aparência**, escolha **Sistema**, **Claro**, **Escuro**, **Meia-noite**, **Floresta**, **Ametista**, **Areia** ou **Alto contraste**, ative uma **cor de destaque** opcional, ajuste o **tamanho do texto** entre 80% e 150% e o **espaçamento das listas** entre Compacta, Padrão e Espaçosa. A troca mostra uma prévia nas janelas abertas. **Aplicar** salva a escolha sem fechar; **Salvar e fechar** salva e encerra o diálogo. **Cancelar**, Esc ou fechar a janela desfaz somente a prévia ainda não aplicada. **Restaurar aparência padrão** volta ao tema Sistema, cor nativa, texto em 100% e espaçamento padrão; aplique essa escolha para mantê-la nas próximas aberturas.

O padrão é **Sistema**, preservando o visual anterior. Claro/Escuro usam paletas neutras e o estilo Qt Fusion; as cores verde/amarelo/vermelho dos diagnósticos permanecem nas tabelas e no Log. No Linux, a preferência é salva em `$XDG_CONFIG_HOME/OPLDeck/OPLDeck.ini`, normalmente `~/.config/OPLDeck/OPLDeck.ini`, na chave `appearance/theme`. Ela não é gravada no pendrive ou nos jogos. A cor personalizada altera destaques e seleções comuns; texto preto/branco é escolhido automaticamente para manter contraste. Diagnósticos e barras de extração/transferência mantêm suas cores. Importação/exportação de temas externos e planos de fundo ficam para outra etapa. Veja [os temas e suas prévias](docs/temas.md) e [renomeação e personalização](docs/opl-deck-e-personalizacao.md).

| Atalho | Ação |
|---|---|
| `Ctrl+O` | Adicionar arquivos ou abrir `ul.cfg` na aba atual |
| `Ctrl+F` | Focar a busca da aba atual |
| `F5` | Reler a lista e atualizar resultados, preservando a seleção quando possível |
| `Ctrl+Enter` | Converter/exportar o jogo ou revisar os vários jogos selecionados |
| `Ctrl+A` | Selecionar as linhas da tabela com foco; só as visíveis entram na transferência |
| `Enter` | Abrir a ficha do jogo |
| `F2` | Renomear o jogo UL selecionado |
| `Delete` | Remover ISO/BIN da lista; em UL, pedir confirmação de exclusão do disco |
| `Ctrl+Shift+C` | Copiar o ID |
| `Ctrl+Shift+P` | Copiar o caminho |
| `Ctrl+Shift+O` | Abrir a pasta do jogo |

Atalhos de seleção funcionam com foco na tabela; ao editar a busca, `Delete` continua editando o texto. Limpar a lista também limpa os filtros.

Se aparecer uma operação pendente, use **Resolver operação pendente…** e escolha a raiz da biblioteca. Esse comando restaura a operação se o índice ainda contém o registro anterior, ou termina a limpeza se a alteração já foi publicada. Ele não reconstrói um índice perdido. Não remova `.opldeck-transaction`: na exclusão, esse diretório pode conter as partes necessárias à restauração.

Para um índice perdido ou corrompido, use **Jogos UL → Reconstruir ul.cfg…**, também disponível no clique direito. Escolha a raiz da biblioteca e revise os grupos encontrados. O programa confere sequência, tamanho e alinhamento das partes, ISO9660 e o ID interno pelo SYSTEM.CNF. Registros legíveis do índice anterior podem preencher título e mídia; nos demais casos, informe o título original e confirme CD/DVD. **O título precisa produzir o CRC presente no nome das partes**. Se você não souber um título compatível, desmarque esse grupo; ele ficará fora do índice, com as partes preservadas.

A reconstrução não renomeia nem apaga partes. Se houver `ul.cfg`, a interface pede confirmação para substituí-lo e cria antes uma cópia exata em `ul.cfg.backup-<identificador>`. Só os jogos selecionados entram no novo índice. Fechar a prévia não altera arquivos; mudanças na biblioteca entre a análise e a gravação exigem uma nova análise. Confira [limites e detalhes](docs/fase-3-recuperacao-indice.md).

## Linha de comando

Os comandos funcionam sem importar Qt:

```bash
python3 -m opldeck inspect /caminho/Jogo.iso
python3 -m opldeck list-ul /caminho/ul.cfg
python3 -m opldeck crc "Gran Turismo 4"
python3 -m opldeck install /caminho/Jogo.iso /destino --title "Meu Jogo" --media DVD
python3 -m opldeck export /destino/ul.cfg SLUS_123.45 /saida/Jogo.iso
python3 -m opldeck inspect-bin /caminho/Jogo.cue
python3 -m opldeck bin2iso /caminho/Jogo.bin /saida/Jogo.iso
python3 -m opldeck rename-ul /destino/ul.cfg SLUS_123.45 "Novo Titulo"
python3 -m opldeck delete-ul /destino/ul.cfg SLUS_123.45 --yes
python3 -m opldeck recover-ul /destino
python3 -m opldeck scan-ul /destino --output plano.json
# Revise plano.json: title, media (CD/DVD) e include (true/false) de cada jogo.
python3 -m opldeck rebuild-ul /destino --plan plano.json --yes
# Se houver ul.cfg e você quiser substituí-lo com backup:
python3 -m opldeck rebuild-ul /destino --plan plano.json --yes --replace
python3 -m opldeck --help
```

Os comandos de inspeção, conversão e gerenciamento produzem JSON. Conversões e gerenciamento mostram progresso no stderr. Falhas retornam código 1; Ctrl+C retorna 130 e informa pendências de recuperação quando houver. `--media` é obrigatório na instalação quando a ISO não está em uma pasta `CD/` ou `DVD/`. `delete-ul` exige `--yes`; sem confirmação, retorna 2 e preserva o jogo.

## Testes

Revisão da 0.9.0 em 28/10/2026: **460 testes aprovados e 2 ignorados**, sem falhas (462 descobertos). As integrações 7z foram executadas com py7zr 1.1.4; somente as duas integrações grandes opt-in não foram repetidas. A revisão corrigiu confirmações, concorrência da leitura USB e resultados de capas, além de otimizar a seleção de lotes grandes. Veja [achados e medição](docs/revisao-publicacao.md) e [cobertura e limitações](docs/versao-0.9.0.md#testes).

```bash
python3 -m unittest discover -v
```

Os testes criam ISOs sintéticas temporárias, conferem offsets UL, entradas corrompidas, limites de partes e fluxos da GUI em modo `offscreen`. Com `cc` instalado, o CRC é comparado com uma implementação C independente, sem tabela. Esse teste é explicitamente ignorado se não há compilador; os testes gráficos são ignorados se não há PySide6.

Os testes de conversão incluem ida/volta, cancelamento, arquivos duplicados, espaço insuficiente, falhas de escrita/publicação, modificações externas e locks. Para executar também a integração com ISOs de 1 GiB, 2,5 GiB e 4 GiB + 2048 bytes:

```bash
OPLDECK_LARGE_TESTS=1 python3 -m unittest tests.test_conversion.LargeConversionTests -v
```

Esse teste usa temporários na pasta do projeto, requer aproximadamente 10 GiB livres e compara SHA-256 da origem e da ISO reconstruída. Os temporários são removidos ao terminar normalmente.

Os testes BIN/CUE usam fixtures sintéticas Mode 1 e Mode 2 Form 1, conferem os dados extraídos byte a byte e com SHA-256, incluindo cruzamento de buffers, setores inválidos no final, CUE incompatível e limpeza da saída após cancelamento/falha.

Os testes de gerenciamento UL verificam preservação de registros, cancelamento, colisões, falhas de escrita/publicação/sincronização/limpeza e recuperação após processos terminados com `os._exit`, antes e depois da publicação do índice. A GUI inclui testes de confirmação, atualização da tabela e recuperação em modo `offscreen`.

Os testes de reconstrução incluem índice ausente/corrompido, títulos desconhecidos ou com CRC divergente, partes inválidas, identificação cruzando partes, backups exatos, mudanças externas, planos desatualizados, cancelamento e processos encerrados antes/depois da publicação.

Os testes de capas e ficha cobrem leitura JPEG/PNG, publicação PNG canônica, redução proporcional, transparência, migração com origem preservada, confirmação, descarte, falhas de gravação/publicação, mudanças externas, links simbólicos, escolha de biblioteca e renomeação UL com capa preservada. A preparação em segundo plano e as fontes têm casos de responsividade, cache limitado, resultados atrasados, pausa, persistência, cancelamento e remoção/remontagem simuladas. A revisão de segurança de 24/10/2026, anterior à 0.9.0, teve **417 aprovados e 13 não executados**: 11 integrações 7z aguardam a biblioteca corrigida e 2 integrações grandes opt-in não foram repetidas. Veja [validação da 0.9.0](docs/versao-0.9.0.md#testes). O [histórico de versões](CHANGELOG.md) permanece preservado.

Os testes de lote incluem cinco jogos em ida e volta, progresso proporcional aos bytes, cancelamento durante/entre jogos, falhas de destino, espaço insuficiente, mudança na origem, BIN/CUE, seleção múltipla e atualização automática da GUI.

Os 17 testes de capas online usam transporte Qt controlado e imagens sintéticas: consulta, erros, limites, cancelamento, persistência e prévia/salvamento. A tentativa externa retornou falha de resolução de `raw.githubusercontent.com`; ainda é necessário validar a consulta real em ambiente com rede disponível.

Os testes de temas cobrem restauração do estilo e da paleta nativos, prévia/cancelamento, persistência, configuração inválida, falha de gravação, atualização de janelas/Log e contraste de texto, inclusive nas linhas selecionadas. Usam configurações temporárias, sem alterar a preferência pessoal do usuário. Suíte padrão da versão 0.8.0: **251 aprovados e 1 integração multi-GiB opt-in ignorada**. A integração opcional usa ISOs sintéticas de 1 GiB, 2,5 GiB e 4 GiB + 2048 bytes e compara SHA-256 após ISO → UL → ISO. Não foi repetida nas etapas de aparência, capas online e leitura automática de pendrive; o espaço insuficiente observado na entrega 0.7.0 está registrado no histórico.

Os testes de dispositivos/capas incluem 25 novos casos de descoberta e identidade de montagem, conexão/remoção simuladas, preservação da fila local, bloqueios de destino, transferência sintética, nomes legados e variantes de caixa. A leitura automática real encontrou 9 jogos UL no pendrive conectado; a pasta ART desse dispositivo estava vazia.

## Consistência das conversões

Partes e ISOs são criadas exclusivamente, sem sobrescrever arquivos. Na instalação, as partes são sincronizadas antes da substituição atômica de `ul.cfg`; os registros anteriores são preservados sem reserialização. Há lock de diretório entre instâncias do OPL Deck. Cancelamento e falhas capturadas removem apenas arquivos criados pela operação.

Feche outros gerenciadores que escrevam no mesmo destino: o lock é cooperativo e não impede ferramentas externas de alterar arquivos. O aplicativo detecta alterações comuns na origem e no índice, mas não oferece isolamento contra toda alteração concorrente externa. Se uma **conversão** sofrer encerramento forçado ou perda de energia, partes sem índice ou uma ISO parcial podem permanecer; conversões ainda não têm recuperação após crash. Esses arquivos não são sobrescritos em uma nova tentativa. Em FAT32, exportar uma ISO acima do limite do sistema de arquivos falha e limpa o parcial; escolha outro sistema de arquivos para ISOs grandes.

Renomeação e exclusão UL usam um diário sincronizado antes de mover as partes. A exclusão guarda as partes no diário até publicar o índice; só então remove os dados. Operações pendentes bloqueiam instalação, exportação e novo gerenciamento na mesma biblioteca. A recuperação confere índice, dispositivo, inode, tamanho e mtime: se houver divergência, preserva os arquivos para análise. Após remontagem, alguns sistemas de arquivos podem mudar essas identidades e exigir intervenção manual. O gerenciamento exige Linux com `renameat2(RENAME_NOREPLACE)` no sistema de arquivos; não usa hard links nem copia as partes. Veja os [detalhes de consistência e limites](docs/fase-3-gerenciamento-ul.md).

## Limites atuais

A [revisão de alinhamento com o OPL](docs/versao-0.9.0.md#compatibilidade-e-capas), baseada no PDF recebido em 23/10/2026, levou à [preparação de capas e aos diagnósticos de nomes/pastas](docs/versao-0.9.0.md#compatibilidade-e-capas). As capas agora são publicadas em PNG canônico com até 512 pixels por lado. ISOs recebem avisos de título acima de 160 bytes, ID divergente, caracteres problemáticos e pasta inadequada, sem renomear ou mover arquivos. Esses avisos não certificam a execução no console.

A transferência continua usando UL. “Destino pronto” verifica montagem/escrita, sem identificar FAT32/exFAT ou a tabela de partição. O usuário reservou esse diagnóstico para a futura seção **Configurações → Pendrive e compatibilidade OPL**, na **versão 9.0**; por enquanto é apenas planejamento. Testes no PS2 continuam pendentes.

Esta entrega está configurada como **0.9.0**, com fila visível, duplicados, capas em lote e unidades de tempo, preservando os recursos da 0.8.0. O AppImage foi construído em Debian 12 e validado com autotestes de arquivos e interface offscreen/X11 virtual. Compatibilidade com desktops e dispositivos reais ainda precisa de verificação. Não há busca nem download de jogos.

- ISO9660 com setores de 2048 bytes e SYSTEM.CNF na raiz. ZSO, arquivos intercalados e múltiplas extensões ainda não são suportados.
- BIN raw: setores de 2352 bytes, modo uniforme 1 ou 2 Form 1, com BOOT2 de PS2. Áudio, PS1, Form 2 e setores sem cabeçalho não são suportados. BIN direto pressupõe uma única faixa de dados; prefira o CUE quando disponível para validar o layout declarado.
- CUE: UTF-8 (BOM opcional), um FILE BINARY, TRACK 01 MODE1/2352 ou MODE2/2352 e INDEX 01 00:00:00. BIN relativo dentro da pasta do CUE. Múltiplas faixas/arquivos, pregaps e outros layouts são recusados.
- A identificação de BIN é parcial: lê apenas os setores necessários ao SYSTEM.CNF. A conversão verifica sincronismo, modo e subcabeçalho XA de todos os setores; não verifica EDC/ECC nem corrige dados corrompidos. O progresso BIN conta bytes ISO gravados e setores processados.
- IDs no formato `SLUS_123.45`; executáveis homebrew com outros nomes não são identificados.
- Títulos UL de 1 a 32 caracteres ASCII imprimíveis. Unicode e títulos maiores são rejeitados, sem alterações silenciosas.
- A mídia usa a pasta `CD/` ou `DVD/`. Fora dela, o tamanho fornece uma **estimativa**, identificada na interface. A heurística do original ainda precisa de validação.
- Campos reservados UL são preservados na serialização; padding de strings é normalizado com zeros ao serializar pelo núcleo. A instalação preserva os registros já existentes como bytes originais.
- Verificar partes confere presença e tamanho, não integridade do conteúdo ou execução no PS2.
- Reconstrução exige nomes canônicos das partes, título ASCII com CRC correspondente e mídia confirmada. Não recupera automaticamente um título desconhecido, não corrige partes corrompidas e não verifica todo o conteúdo por hash. A análise lê no máximo 100.000 arquivos com prefixo UL; planos CLI aceitam até 64 MiB.
- Não houve comparação com o executável original nem validação de execução em console. Houve leitura automática de um pendrive real com 9 registros UL, sem alterar seus arquivos; as transferências desta etapa foram testadas com imagens sintéticas.
- Capas: apenas frontal `COV`. Entrada JPEG/PNG até 20 MiB e 4096 pixels por dimensão; saída PNG RGB/RGBA de 8 bits, proporcional e limitada a 512 pixels por lado. Esse teto conservador não é um perfil validado para cada tema/fork do OPL. Sem PNG canônico, várias origens por ID bloqueiam a edição até resolver a ambiguidade. Links simbólicos em `ART` ou no arquivo da capa são recusados. A publicação usa temporário e substituição atômica; preserva origens legadas ao criar o PNG, mas não mantém histórico das substituições do próprio PNG. O lock é cooperativo e não impede todas as alterações concorrentes por outros programas.
- O log é mantido em memória, limitado às últimas 10.000 linhas; use **Salvar log…** para persistir antes de fechar. A exportação de log também exige um arquivo novo.

## Organização

`opldeck/`: núcleo, CLI e Qt. `tests/`: testes e fixtures. `tools/`: referência C. `docs/`: decisões de implementação.

Veja [as decisões iniciais](docs/decisoes-iniciais.md) e o [histórico público](CHANGELOG.md).

Detalhes da versão 0.2.0 e validação: [conversões e log técnico](docs/fase-2-conversao-e-log.md).

Detalhes da versão 0.3.0: [BIN/CUE → ISO](docs/fase-3-bin-cue.md).

Detalhes da versão 0.4.0: [gerenciamento UL e recuperação de operações](docs/fase-3-gerenciamento-ul.md).

Detalhes da versão 0.5.0: [reconstrução de ul.cfg](docs/fase-3-recuperacao-indice.md).

Detalhes da versão 0.6.0: [ficha, capas locais e provedores](docs/ficha-e-capas-locais.md).

Detalhes da versão 0.7.0: [transferências em lote e progresso](docs/transferencias-em-lote.md).

Aparência e preferências: [personalização](docs/opl-deck-e-personalizacao.md).

Versão atual: [notas da 0.9.0](docs/versao-0.9.0.md) e [fila, duplicados e capas](docs/fila-duplicados-capas.md).

Documentação pública: [Manual passo a passo](tutorial/MANUAL.md) e [Referência de uso](tutorial/referencia-de-uso.md).
