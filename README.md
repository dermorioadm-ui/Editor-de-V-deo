# Sharkcut

**Sharkcut** é o editor de vídeo local deste repositório: você joga o vídeo
bruto na página, escolhe um preset, aperta **EDITAR** e recebe o vídeo
cortado, acelerado e legendado. Depois revisa e ajusta o que ficou fora do
lugar.

**O arquivo nunca sai da sua máquina.** Não existe upload, não existe nuvem, não
existe limite de tamanho. O programa roda no seu computador e lê o vídeo direto
da pasta onde ele já está.

> Uma ressalva honesta, e só uma: a aba **IA** é opcional, vem desligada e não
> funciona sem uma chave do Gemini que você mesmo cole. Ligada, ela manda o
> **texto** da transcrição — e, se você pedir ajuda com anexos, um quadro de
> 360 px de cada anexo seu. **O vídeo continua não saindo**, nem o arquivo nem
> o caminho dele. Detalhes, inclusive o que a Google faz com isso no plano
> gratuito, em [12. A aba IA](#12-a-aba-ia).

---

## Índice

1. [O que você precisa instalar](#1-o-que-você-precisa-instalar)
2. [Instalar no Windows, passo a passo](#2-instalar-no-windows-passo-a-passo)
3. [Instalar no macOS](#3-instalar-no-macos)
4. [Abrir o editor](#4-abrir-o-editor)
5. [Como usar](#5-como-usar)
6. [A GPU está sendo usada?](#6-a-gpu-está-sendo-usada)
7. [A transcrição está lenta](#7-a-transcrição-está-lenta)
8. [Onde ficam os arquivos](#8-onde-ficam-os-arquivos)
9. [Problemas comuns](#9-problemas-comuns)
10. [Como funciona por dentro](#10-como-funciona-por-dentro)
11. [Ajustes por variável de ambiente](#ajustes-por-variável-de-ambiente)
12. [A aba IA](#12-a-aba-ia)

---

## 1. O que você precisa instalar

Três coisas. Uma vez só, na primeira vez.

| O quê | Para quê | Tamanho |
|---|---|---|
| **Python 3.10 ou mais novo** | é a linguagem em que o editor foi escrito | ~30 MB |
| **ffmpeg** | é quem realmente corta, acelera e encoda o vídeo | ~150 MB |
| **O modelo de transcrição** | é quem escuta o áudio e escreve as palavras | ~1,6 GB (baixa sozinho na primeira vez) |

Se você nunca instalou nada disso, siga a seção 2 na ordem. Não pule etapas.

---

## 2. Instalar no Windows, passo a passo

### 2.1 — Instalar o Python

1. Abra <https://www.python.org/downloads/windows/>
2. Clique no botão grande **Download Python 3.12** (ou 3.11, 3.13 — qualquer um
   a partir do 3.10 serve).
3. Abra o arquivo baixado.
4. **MUITO IMPORTANTE:** na primeira tela, marque a caixinha
   **“Add python.exe to PATH”**, lá embaixo. Se você não marcar, nada vai
   funcionar depois e você vai ter que desinstalar e refazer.
5. Clique em **Install Now** e espere.
6. No fim, clique em **Close**.

**Como conferir se deu certo:** aperte a tecla ⊞ Windows, digite `powershell`,
abra o **Windows PowerShell**, digite isto e aperte Enter:

```powershell
python --version
```

Tem que aparecer algo como `Python 3.12.4`. Se aparecer erro ou abrir a Microsoft
Store, o Python não foi instalado ou você esqueceu de marcar o “Add to PATH”.
Desinstale pelo Painel de Controle e refaça o passo 4.

### 2.2 — Instalar o ffmpeg

O ffmpeg é a peça mais importante. Sem ele o editor não abre.

#### Jeito fácil (recomendado)

No PowerShell, cole isto e aperte Enter:

```powershell
winget install --id Gyan.FFmpeg -e
```

Espere terminar. Depois **feche o PowerShell e abra de novo** (isso é necessário
para o Windows enxergar o programa novo).

#### Jeito manual (se o winget não existir na sua máquina)

1. Abra <https://www.gyan.dev/ffmpeg/builds/>
2. Na seção **release builds**, baixe o arquivo
   **ffmpeg-release-essentials.zip**.
3. Clique com o botão direito no arquivo baixado → **Extrair tudo**.
4. Dentro da pasta extraída existe uma pasta chamada `bin`, com três arquivos:
   `ffmpeg.exe`, `ffprobe.exe` e `ffplay.exe`.
5. Crie a pasta `C:\ffmpeg` e mova a pasta `bin` inteira para lá. O caminho final
   tem que ficar exatamente assim: `C:\ffmpeg\bin\ffmpeg.exe`.

   > O editor procura sozinho em `C:\ffmpeg\bin`. Se você colocar aí, já
   > funciona e você pode pular o passo 6.

6. *(Opcional, para o ffmpeg funcionar em qualquer lugar)* Aperte ⊞ Windows,
   digite `variáveis de ambiente`, abra **Editar as variáveis de ambiente do
   sistema** → botão **Variáveis de Ambiente** → na lista de baixo selecione
   **Path** → **Editar** → **Novo** → cole `C:\ffmpeg\bin` → **OK** em todas as
   janelas. Feche e abra o PowerShell de novo.

**Como conferir se deu certo:**

```powershell
ffmpeg -version
```

Tem que aparecer `ffmpeg version 6.x` (ou 7.x) e um monte de texto. Se aparecer
“não é reconhecido como um cmdlet”, o ffmpeg não está no PATH — use o jeito da
pasta `C:\ffmpeg\bin`, que o editor acha sozinho.

### 2.3 — Instalar o editor

1. Baixe este projeto e extraia numa pasta, por exemplo
   `C:\Users\SeuNome\sharkcut`.
2. Dê um duplo clique em **`instalar.bat`**.

Abre uma **janela de instalação**: ela confere Python, espaço em disco e
ffmpeg, mostra a instalação acontecendo linha a linha e, no fim, cria os
atalhos. As caixinhas já vêm marcadas:

| | |
|---|---|
| Ícone na Área de Trabalho | atalho com o tubarão |
| Ícone no Menu Iniciar | mais um para o `limpar.bat` |
| Fixar na barra de tarefas | tenta fixar sozinho — veja abaixo |
| Abrir o Sharkcut ao terminar | |

**Sobre o ícone.** Um `.bat` não carrega ícone: o Windows desenha o do
`cmd.exe` e não há como mudar isso. Quem carrega ícone é o **atalho** — e a
janela preta aberta por ele herda esse ícone, então é o tubarão que aparece na
barra de tarefas enquanto o editor roda. Por isso o instalador cria um
`Sharkcut.lnk` até dentro da própria pasta do programa: é nele que você clica,
não no `iniciar.bat`.

**Sobre fixar na barra.** A Microsoft bloqueou fixar por script do Windows 10
1803 em diante. O instalador tenta (ainda funciona em máquina mais antiga) e
**confere se pegou** em vez de mentir. Se não pegar, o caminho é um clique:
abra o Sharkcut, clique com o botão direito no ícone dele na barra e escolha
*Fixar na barra de tarefas*.

> Se o PowerShell estiver bloqueado na sua máquina, o `instalar.bat` cai
> sozinho no instalador de texto (`instalar-console.bat`), que faz exatamente
> a mesma coisa sem janela — só sem os atalhos.

Prefere fazer na mão? No terminal, dentro da pasta:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
```

Isso baixa umas 400 MB e demora alguns minutos. É normal.

> **O que é `.venv`?** É uma pasta que guarda as bibliotecas só deste programa,
> separadas do resto do computador. Se um dia quiser desinstalar tudo, é só
> apagar a pasta do projeto.

---

## 3. Instalar no macOS

Abra o **Terminal** (⌘+espaço, digite “Terminal”) e cole os comandos, um por vez:

```bash
# 1) Homebrew, se você ainda não tiver
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2) ffmpeg e Python
brew install ffmpeg python@3.12

# 3) dentro da pasta do projeto
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Para abrir o editor depois: `.venv/bin/python -m editor`
(ou dê um duplo clique em `iniciar.command`).

---

## 3.5 — Onde isso roda, afinal?

Curto: **roda no seu computador.** A tela é uma página de navegador, mas quem
serve essa página é um programa rodando no seu próprio PC. Por isso o endereço
é `localhost` — que quer dizer, literalmente, "esta máquina aqui".

```
   seu PC
   ┌─────────────────────────────────────────────┐
   │  iniciar.bat  →  programa Python + ffmpeg   │
   │        │                                    │
   │        ├── lê o vídeo direto da sua pasta   │
   │        ├── transcreve, corta, encoda        │
   │        └── serve a tela em localhost:8000 ──┼──→ seu navegador
   └─────────────────────────────────────────────┘
              nada disso passa pela internet
```

A internet é usada **uma única vez**: no primeiro EDITAR, para baixar o modelo
de transcrição. Depois disso você pode desligar o wi-fi que continua
funcionando — a não ser que você ligue a aba IA, que é a única parte do
programa que fala com fora. Ver [12. A aba IA](#12-a-aba-ia).

### Dois jeitos de importar, e os dois não copiam nada

**Arrastando.** Solta o arquivo na página (ou no trilho certo, se for música ou
anexo). O editor procura ele pelo nome nas suas pastas de sempre.

**Pela janela do Windows.** Botão **Escolher no computador** — abre o diálogo de
sempre, o mesmo de qualquer programa. É também o que aparece sozinho quando o
arrasto não acha o arquivo.

Nos dois casos **o arquivo não é copiado nem enviado**: o que o editor guarda é
o caminho. Por isso o arrasto precisa achar o arquivo — o navegador entrega só
o nome e o tamanho, nunca o caminho, e copiar 2 GB à toa está fora de questão.
A janela do sistema não tem esse problema: ela devolve o caminho de verdade.

### Antes de gerar: velocidade e zoom

Na tela inicial, dois controles que valem **antes** do EDITAR, porque mudam o
plano inteiro (refazer depois custa uma volta do pipeline):

- **Velocidade** — multiplica o que cada etapa já pede. Em 0% cada bloco fica
  com a velocidade da própria etapa (Explicação acelera mais que CTA, por
  exemplo). Em +10%, tudo sobe 10% em cima disso.
- **Zoom entre cenas** — de *quase parado* a *agressivo*. É o quanto o
  enquadramento muda a cada corte. Medido: em 0 a escada inteira fica em
  1,00x (imagem parada); em 2,0 a variação dobra a do preset.

### A prévia que não trava

Duas prévias, cada uma para uma coisa — e a razão veio de medição que inverteu
o meu diagnóstico:

**Baixar resolução não resolvia o travamento.** A cópia de 480p já decodifica
122x mais rápido que o tempo real; a 240p vai a 150x. Resolução nunca foi o
gargalo. O que trava é o **pulo**: tocar a edição sobre o arquivo da fonte é
uma busca por bloco (~55 ms cada), e o corte de silêncio produz um bloco a
cada poucos segundos. Tranco, tranco, tranco.

A cura é **renderizar a edição**: um arquivo linear, com zero buscas. Custa
7,5 s para 38 s de vídeo e entra no clique único. É essa que você assiste — e
como o zoom e a legenda já vêm queimados, **o que você vê é o que vai baixar**.

Quando você edita, ela envelhece e se refaz sozinha depois de uns segundos
parado; enquanto isso o player cai na cópia leve da fonte, que acompanha na
hora. O chip no canto diz qual das duas está tocando.

### A cópia leve da fonte

Tocar um arquivo de 2 GB direto no navegador engasga. Na primeira análise o
editor faz uma **cópia leve** da fonte e é ela que toca — o mesmo truque do
CapCut. Medido num vídeo de 60 s, 1080x1920 a 13 Mbps (98 MB):

| lado maior | tamanho | custo de buscar |
|---|---|---|
| 854 | 3,18 MB | 74 ms |
| **480** | **1,76 MB** | **64 ms** |
| 360 | 1,40 MB | 64 ms |

480 é onde a curva vira: **56x menor** que a fonte, e ainda dá para reconhecer
rosto e boca — que é tudo o que a prévia precisa. A exportação continua saindo
da fonte, em qualidade cheia. Um chip **prévia leve** aparece no canto quando
ela está em uso.

A prévia toca a **edição**, não o arquivo cru: ela pula o que foi cortado e
mostra a legenda no lugar, então dá para ir editando e conferindo ao mesmo
tempo.

### A legenda da prévia é a mesma da exportação

Isso deu trabalho e vale explicar. O fontsize do ASS **não é** font-size de CSS:
o libass imita o GDI e escala a fonte para que *ascent − descent* caiba no
fontsize, enquanto o CSS escala pelo *em*. Para Arial dá 2048/2288 = **0,895**.
Medido com o filtro `ass` do próprio ffmpeg:

| | medido |
|---|---|
| altura de maiúscula | 0,640 × fontsize |
| avanço de linha | 1,000 × fontsize (exato) |
| contorno | cresce 1,0 × outline **para fora** de cada lado |
| base da tinta | margin_v + 0,172 × fontsize do fundo |

Com essas quatro constantes a prévia bate com a exportação dentro de **1,9% na
altura e 0,8 px na posição** — e tem regressão medindo isso a cada rodada.

O erro grande, porém, era outro: a régua era a altura do **elemento de vídeo**.
Com a prévia leve tocando, o elemento tem 480 de altura contra 1920 da fonte, e
o estilo está medido na fonte — a legenda saía **4x maior**. A régua certa é a
resolução da fonte, que é a mesma PlayRes que o ASS usa.

### Trilha de fundo

Três jeitos: arraste o MP3 para o trilho **Trilha** (A1), clique no **+** do
trilho, ou vá em **Mídia → + música**. Os dois últimos abrem a janela do
Windows. O arquivo **não é copiado nem enviado**: o editor só guarda o caminho.

Depois, no painel da direita: volume, **mudo**, e o *ducking* (a trilha abaixa
sozinha quando você fala). Para mudar onde ela entra e onde termina, arraste o
bloco no trilho, ou as bordas dele.

### Três jeitos de usar

| Modo | Como | Quando serve |
|---|---|---|
| **Só neste PC** (padrão) | `iniciar.bat` → `localhost:8000` | o normal |
| **Abrir do celular** | `iniciar-rede.bat` → o endereço que ele mostrar | revisar o corte deitado no sofá; o vídeo continua no PC |
| **100% na internet** | não é este programa — veja abaixo | outra ferramenta, outro preço |

### E rodar tudo na internet?

Dá para fazer, mas seria **um produto diferente**, e ele briga com a primeira
regra deste aqui: *o arquivo nunca sai da minha máquina*.

Para funcionar na nuvem, cada vídeo teria que **subir inteiro** antes de
qualquer coisa acontecer:

| Seu upload | 2 GB demoram |
|---|---|
| 50 Mbps (fibra boa) | ~5 minutos |
| 20 Mbps | ~14 minutos |
| 10 Mbps | ~27 minutos |

E isso é só a subida — antes de transcrever, cortar ou encodar. Depois você
ainda baixa o resultado. Hoje esse tempo é **zero**, porque o programa lê o
arquivo direto da pasta onde ele já está.

Fora o upload, um servidor que aguente 1080x1920 a CRF 15 com GPU custa por
hora, e o código todo é construído em cima de caminho de arquivo local
(`C:\Users\...\vsl.mp4`) — não existe nem endpoint de upload.

**O meio-termo que resolve quase sempre:** rode no PC e abra do celular com o
`iniciar-rede.bat`. Você revisa e ajusta de onde quiser dentro de casa, e o
arquivo de 2 GB nunca sai do lugar.

## 4. Abrir o editor

**Windows:** duplo clique em **`iniciar.bat`**.

### Atualizar (e por que o disco não enche mais)

Baixe o ZIP novo, extraia **por cima** e rode o `instalar.bat`. Ele não baixa
nada de novo: o ambiente Python é **um só**, guardado em
`AppData\Local\Editor de Vídeo\venv`, fora da pasta do programa.

Isso é conserto de um erro de projeto real. Antes o ambiente era criado
**dentro** de cada pasta extraída — quem atualizava dez vezes ficava com dez
cópias de ~3 GB paradas no disco, e a instalação seguinte morria no meio do
download com `No space left on device`. Sem explicação nenhuma na tela.

Agora o instalador **confere o espaço antes de começar**: uns 6 GB para uma
instalação do zero (ambiente + modelo de transcrição), mas só **1 GB quando o
ambiente já existe** — atualizar não baixa quase nada, e exigir 6 GB aí
travava quem tinha tudo instalado e 4 GB livres. Se faltar, ele manda você
rodar o **`limpar.bat`**.

As versões antigas **não aparecem em "Aplicativos" do Windows**: cada uma é
só uma pasta, e o que pesa é o ambiente Python que ficou dentro dela. O
`limpar.bat` procura essas cópias **no computador inteiro** (Área de
Trabalho, Downloads, Documentos, OneDrive, a raiz de cada disco), mostra o
tamanho de cada uma e pergunta em duas etapas: primeiro os ambientes (o
peso), depois as pastas inteiras (as versões antigas em si). Nada é apagado
sem você confirmar.

**Apagar a pasta não limpa tudo.** Sobra peso em cinco lugares, e o
`limpar.bat` acha os cinco, mostra o tamanho de cada um e pergunta **um por
um**:

| | |
|---|---|
| cópias antigas do editor (com o `.venv` de ~3 GB dentro) | procuradas no computador inteiro, não só perto desta pasta |
| ambiente compartilhado órfão | ~3 GB |
| cache do pip | 1 a 5 GB |
| projetos de teste | varia |
| **modelos de transcrição sobrando** | **até 10 GB** |
| **cache bruto de download (`xet`)** | **até 8 GB** |

Os dois últimos costumam ser os maiores e são os menos óbvios.

O **cache do `xet`** é o que mais engana: ele guarda os *pedaços brutos* do
download **além** do modelo montado — o mesmo dado, duas vezes. Numa medição
real, a pasta inteira somava 9,36 GB enquanto os modelos explicavam só
1,78 GB; os ~7,5 GB restantes eram isso. O app usa **um** modelo —
`large-v3` se houver placa de vídeo, `turbo` se for processador — mas cada
instalação antiga pode ter baixado um diferente. Num caso real apareceram
9,36 GB acumulados. O modelo **em uso** nunca é oferecido para apagar, e
modelos de qualquer **outra** ferramenta de IA sua também não.

As travas: uma pasta só entra na lista se **provar ser cópia deste editor**
(tem `requirements.txt` e `editor/__init__.py`), então o ambiente de outro
projeto Python seu fica intocado. **Seus vídeos originais nunca saíram da
pasta onde você gravou**, e a sua chave do Gemini não é tocada.

**macOS:** duplo clique em **`iniciar.command`**.

Para abrir também do celular (mesma rede): **`iniciar-rede.bat`**. Ele mostra
o endereço a digitar no outro aparelho, tipo `http://192.168.0.15:8000`.

Ou, no terminal, dentro da pasta do projeto:

```powershell
.venv\Scripts\python -m editor
```

O programa mostra um resumo assim:

```
==============================================================
 Editor de Vídeo — tudo roda na sua máquina
==============================================================
 ffmpeg .......... OK  (ffmpeg version 7.1)
 transcrição ..... OK  (faster-whisper, modelo large-v3, cuda/float16)
   1 GPU(s) CUDA visível(is) para o CTranslate2

 Abrindo http://localhost:8000
 Para parar: Ctrl+C nesta janela
```

O navegador abre sozinho em **http://localhost:8000**.

**Deixe essa janela preta aberta enquanto estiver usando o editor.** Fechar a
janela desliga o programa. Para desligar de propósito, clique nela e aperte
**Ctrl+C**.

> **Conferir se está tudo instalado, sem abrir o editor:**
> `.venv\Scripts\python -m editor --check`
>
> **Provar que a máquina consegue mesmo cortar e exportar:**
> `.venv\Scripts\python -m editor --test`
>
> O autoteste cria um vídeo de teste, analisa, corta, exporta e confere o
> resultado — leva menos de um minuto e não precisa de internet. Se ele passar,
> o problema não está na instalação.

---

## 5. Como usar

### O clique único

1. **Arraste o vídeo para a página.** O navegador não entrega o caminho do
   arquivo por segurança, então o editor procura pelo nome nas suas pastas de
   vídeo (Vídeos, Área de Trabalho, Downloads, Documentos). Se ele não achar,
   clique em **Escolher no disco** e aponte o arquivo. Nos dois casos **o
   arquivo não é copiado nem movido** — o editor só anota onde ele está.
2. **Escolha o preset:**
   - **VSL** — 2 a 3 minutos. Corte conservador, velocidade contida.
   - **Criativo 60s** — corte agressivo, até 1,18x, legenda maior.
   - **Story** — 30 segundos, corte máximo, até 1,25x.
3. **Clique em EDITAR.** A barra mostra em que passo está: extraindo áudio →
   transcrevendo → analisando → propondo cortes → gerando legendas.
4. Quando terminar, o vídeo aparece na timeline, já cortado, acelerado e
   legendado.
5. O editor já tirou os takes ruins (com e sem palma), acertou as bordas e
   montou o jogo de zoom — sem perguntar nada.
6. **Olhe a faixa logo abaixo do nome do projeto.** É o veredito:
   - verde, “✓ Pronto para exportar” — não sobrou nada para você decidir.
     Clique em **exportar →** e acabou.
   - amarelo, “Falta você decidir N coisa(s)” — diz exatamente o que é. Só
     aparece quando o editor tentou resolver sozinho e não deu.

### Revisar

- **Aba Texto** — a transcrição inteira. Clique numa palavra, Shift+clique para
  estender a seleção, **Delete** para tirar aquele trecho do vídeo. O texto
  removido fica riscado (dá para recuperar). Muletas como “simplesmente”,
  “então”, “né” vêm sublinhadas: pontilhado amarelo = dá para tirar; ondulado
  vermelho = tirar quebra a palavra vizinha, é melhor manter.
- **Timeline** — arraste sobre a forma de onda para selecionar e aperte
  **Delete**. A roda do mouse dá zoom (de 1 segundo até o vídeo inteiro),
  Alt+arraste move. São quatro faixas, de cima para baixo:

  | faixa | o que é |
  |---|---|
  | marcas | bandeirinha laranja = palma (clique liga/desliga). Triângulo vermelho = borda que ainda precisa de você. |
  | onda | o áudio. Hachurado vermelho = sai do vídeo — **arraste as bordas vermelhas** para tirar mais ou devolver. Cinza = take descartado pela palma. O risco vermelho fino no rodapé é um corte curto demais para desenhar — dê zoom para vê-lo. |
  | seções | Gancho, Dor, Oferta… blocos vizinhos da mesma seção viram uma faixa só, com o nome escrito. |
  | velocidade | um retângulo por bloco, colorido pela velocidade: azul 1,00x, verde até 1,12x, amarelo até 1,25x, laranja acima disso. Canto marcado = enquadramento fechado. Clique para selecionar; **Delete apaga o bloco inteiro**. |

  Embaixo de tudo, a faixa azul das legendas — arraste as bordas para ajustar.
### Filtros de cinema

Aba **Filtro**. Oito opções, e a miniatura de cada uma é o **seu vídeo** com o
filtro já aplicado — você escolhe olhando, não lendo um nome.

| filtro | para quê |
|---|---|
| Preto e branco | o clássico de depoimento |
| Preto e branco duro | alto contraste, quase gráfico; bom para hook |
| Ambiente quente | pele viva, sombra âmbar; sala de casa, fim de tarde |
| Ambiente frio | azulado e sóbrio; escritório, madrugada |
| Cinema (teal & orange) | pele quente contra fundo esverdeado; look de trailer |
| Vintage | lavado, com sépia no preto; parece arquivo antigo |
| Nítido | só contraste e definição; salva vídeo de celular chapado |

Todos entram no **mesmo encode** dos blocos: nenhuma geração a mais, e a
legenda não é afetada (o filtro entra antes dela, senão o contorno preto
sumiria junto). A vinheta de cada filtro pode ser forçada no controle abaixo.

- **Painel da direita** — o que sobrou para você decidir. Bordas que dava para
  acertar sozinho já vêm acertadas (o painel verde diz quantas e o que mudou);
  onde a fala não tem respiro por perto, o editor **não corta** em vez de comer
  palavra, e só chama você quando nem isso resolve.
- **Aba Legendas** — editar o texto, arrastar as bordas, mudar fonte, cor,
  tamanho e posição, calibrar o tamanho por largura em pixels, e o dicionário de
  correções (vale entre projetos).
- **Ctrl+Z / Ctrl+Shift+Z** — desfazer e refazer, com histórico completo.
- **Prévia 480p** — a prévia normal toca o arquivo original pulando os cortes,
  sem renderizar nada. Se o seu navegador não souber tocar o formato do vídeo,
  clique em **prévia 480p** abaixo do player: ele renderiza uma versão leve só
  para você assistir. A exportação final continua em qualidade cheia.
- **Aba Áudio → calibrar de-esser** — se você aumentar o realce de presença, os
  “s” tendem a ficar agressivos. Esse botão mede a sibilância antes e depois e
  procura sozinho a intensidade de de-esser que devolve o som ao nível original.

### O protocolo FALADO: "corta" e "ok" (o principal)

O jeito mais confiável de marcar a gravação é com a boca, porque a
transcrição já traz a palavra com o tempo exato — não existe falso positivo
de acústica, microfone ruim nem calibração:

| você diz | o que acontece |
|---|---|
| **"corta"** (ou "apaga", "errei", "refaz", "descarta") | a tentativa em andamento sai sozinha; refaça em seguida |
| **"próximo"** (ou "seguinte") | trava: **nada antes daqui é tocado**, nem pela IA |

A palavra tem que ser dita **sozinha**, com uma pequena pausa antes e depois —
"corta" dentro de uma frase é conteúdo e fica.

**Por que "próximo" e não "ok".** Foi medido, e o resultado inverteu a escolha
original. O detector procura a palavra dita SOZINHA, entre pausas — e é
exatamente esse o jeito natural de falar "ok", "boa" e "fechou". Numa amostra
de 60 linhas de copy de anúncio:

| palavra | no meio da frase | **dita sozinha** | |
|---|---|---|---|
| ok | 0 | **1** — *"Ok, mas e se eu já declarei?"* | arriscado |
| boa | 1 | **1** — *"Boa, agora você já sabe."* | arriscado |
| fechou | 0 | **1** — *"Fechou? Então clica agora."* | arriscado |
| **próximo** | 2 | **0 — nunca** | bom |
| **corta** | 1 | **0 — nunca** | bom |

A contagem crua engana ("próximo" aparece MAIS na copy que "ok"), mas no meio
da frase o isolamento protege. O que derruba um comando é ele ser **marcador
de discurso** — as palavras que a gente diz soltas. Comando tem que ser
palavra de **conteúdo**.

**O que cada um serve.** "Corta" marca a EXCEÇÃO: você errou. "Próximo" é
outra coisa — é uma **trava**: o trecho antes dele fica intocável, nem a IA
que corta copy mexe. Use depois de uma frase que você não quer que ninguém
encoste. Não é obrigatório em toda frase boa: acertar é o padrão, só o que
foge do padrão precisa de marca. As palavras de comando **nunca
aparecem no vídeo nem na legenda**: são instrução, não fala. E a IA que decide
os cortes vê `[CORTA]` e `[OK]` escritos na transcrição, no lugar exato.

Palma e assobio continuam funcionando como alternativa de mão ocupada.

### O vídeo abre PRONTO

Soltou o arquivo, o editor não aparece pela metade: uma tela de progresso
mostra as cinco etapas — ouvir o áudio, transcrever, cortar (IA + marcadores),
câmeras e legendas, prévia — e **o editor só abre com o vídeo pronto**:
cortado, legendado, com o jogo de câmeras decidido e a prévia leve gerada.
Editar é retoque, não trabalho.

O preview toca a EDIÇÃO (pula o que saiu, mostra a legenda queimada na régua
certa) e agora mostra também o **jogo de câmeras ao vivo**: o mesmo recorte
concêntrico no rosto que a exportação aplica, trocando seco no corte.

### O protocolo da palma

Se você errar uma frase durante a gravação: **bata uma palma, conte até três e
refaça a frase inteira.** O editor detecta a palma e descarta sozinho a frase que
estava em andamento (não tudo que veio antes — só a tentativa que deu errado).

Os takes descartados aparecem em cinza na timeline. Se o take novo tiver saído
pior que o antigo, passe o mouse por cima e clique em **recuperar este take**.

### O protocolo do assobio

O assobio é o **contrário** da palma:

| | o que significa | o que acontece |
|---|---|---|
| **palma** | errei | joga fora a frase que eu estava falando |
| **assobio** | acertei | fecha aqui — o vazio até a próxima palavra sai |

Assobie quando terminar uma frase boa. O corte cola na última palavra, e todo o
silêncio até você voltar a falar sai — **três segundos ou sessenta, tanto faz**.
É isso que te deixa respirar, beber água e recomeçar sem pressa.

Os assobios aparecem em verde na régua de marcas, com a bandeirinha virada para
a esquerda (a da palma vira para a direita). No painel da direita dá para
desligar um que não era assobio.

**Calibração.** Assobio varia muito de pessoa para pessoa — uns fazem 1 kHz,
outros 3 kHz. Se este vídeo tiver dois assobios ou mais, o botão **calibrar**
guarda a SUA frequência e a busca fica bem mais estreita dali em diante. Não
precisa gravar nada à parte: sai de graça do arquivo que você já soltou.

**Como ele separa assobio de palma, de fala e de chiado.** Pela CONCENTRAÇÃO:
quanto da energia cabe numa faixa de ±4% em volta da frequência de pico. É a
definição direta de “isto é um tom”. Medido:

| | concentração |
|---|---|
| palma | 0,073 – 0,081 |
| fala | 0,103 |
| fricativas /s/, /f/, /ʃ/ | 0,057 – 0,077 |
| **assobio** | **0,464 – 0,966** |

A margem é de 4,5x entre o pior assobio e o caso não-assobio mais próximo. O
mesmo número serve dos dois lados: um som tonal **nunca** é lido como palma, o
que impede o assobio de apagar a frase que ele acabou de aprovar.

### Corte mais em cima

No painel da direita, o card **Corte** tem um controle só — para a direita, a
pausa que vira corte fica menor e sobra menos ar nas pontas:

| | pausa que vira corte | ar por lado |
|---|---|---|
| respira (0%) | 0,90 s | 0,32 s |
| meio (50%) | 0,59 s | 0,20 s |
| cola (100%) | 0,28 s | 0,07 s |

Junto dele vem **medir a pausa na minha fala**, ligado por padrão. Quem fala
devagar tem pausa longa *dentro* da frase, e um número fixo ou come a frase ou
deixa o vale. Medido na sua fala, o piso sai sozinho: 0,53 s para quem fala
devagar contra 0,22 s para quem fala rápido, no mesmo preset.

### O que o editor tira sozinho

Três coisas saem do vídeo sem perguntar nada:

| o quê | como ele sabe |
|---|---|
| **o take que você refez batendo palma** | o timbre da palma (tabela abaixo). Descarta a frase que estava em andamento — a última palavra dita antes da palma manda, não a respirada. |
| **o vazio depois de uma palma ou de um assobio** | o corte cola na fala dos dois lados, por mais que você demore para voltar. |
| **o take que você refez SEM bater palma** | você disse quase a mesma coisa duas vezes seguidas. Fica a **última**; a primeira foi a que deu errado. |
| **a borda de corte que encostava em fala** | acerta sozinho; e onde não dá para cortar limpo, não corta — a pausa fica. |

Tudo isso aparece no painel da direita em **“Saiu sozinho”**, com o texto do
que saiu riscado e o texto do que ficou embaixo. Errou em algum? **voltar**, e
ele volta. Você corrige uma automação; nunca alimenta uma.

**Como ele separa refeitura de repetição de propósito.** Comparar as palavras
inteiras não basta: “eu vou te mostrar o print DA CONTA” e “…DO EXTRATO” batem
75% e são duas frases diferentes. O que decide é a semelhança das palavras de
**conteúdo** (fora artigo, preposição e pronome). Medido em 16 pares: refeitura
fica entre 0,80 e 1,00; frase diferente, entre 0,00 e 0,67 — e “não é sobre
**preço** é sobre **valor**” contra “não é sobre **sorte** é sobre **método**”
dá 0,00 de conteúdo, então o paralelismo fica no vídeo.

### Por que o corte de silêncio às vezes não acontecia

O Whisper não devolve fronteira acústica: devolve fronteira de **alinhamento**.
Quando ele erra, uma palavra de duas letras vem ocupando cinco segundos — e
esses cinco segundos são, na prática, uma pausa escondida dentro de uma
palavra. Como o corte nasce do **buraco entre palavras**, não havia buraco,
não havia corte, e o vale ia inteiro para o vídeo.

Duas defesas independentes, e cada uma sozinha já resolve:

1. **Encaixe no som.** Cada palavra é encolhida até onde há áudio de verdade.
   Só encolhe, nunca estica — encolher no máximo deixa o corte conservador;
   esticar restauraria silêncio.
2. **Rede de segurança.** Se ainda sobrar um vale dentro de um trecho, ele é
   partido pelo **envelope**, custe o que custar ao que as palavras dizem.

Medido num caso com quatro palavras esticadas: **11,6 s de vale** ficavam no
vídeo; com qualquer uma das duas defesas, **zero**. O painel da direita conta
quantas palavras foram encaixadas.

### Zoom entre cenas — multicâmera simulada

O vídeo é um take só, câmera fixa. O zoom é **recorte digital**: recorta-se
uma área menor do quadro e reescala de volta. Trocar essa área periodicamente
cria a impressão de troca de plano.

A regra que sustenta tudo: **a troca só acontece em cima de um corte.** Durante
fala contínua o olho lê como salto; exatamente no corte, lê como câmera nova.

**O critério é tempo de tela acumulado**, não troca de frase. Trocar a cada
bloco fazia o enquadramento piscar duas ou três vezes por segundo — o corte de
silêncio produz blocos de 0,13 s. Agora o tempo acumula e a cena vira no corte
mais próximo do alvo.

| | VSL | Criativo 60s | Story |
|---|---|---|---|
| segundos por enquadramento | 4,5 | 3,2 | 2,5 |
| amplitude da escada | ±0,08 | ±0,14 | ±0,18 |
| zoom máximo | 1,15x | 1,20x | 1,25x |

**A escada não é sorteada.** É uma sequência fixa que alterna e volta ao
neutro: `1,00 · 1,08 · 1,00 · 1,14 · 1,05 · 1,17 · 1,00 · 1,11 · 1,06 · 1,14`.
O 1,00 reaparece porque voltar ao plano aberto dá respiro — escada que só fecha
sufoca o vídeo. Duas cenas vizinhas nunca ficam a menos de 0,05 uma da outra:
abaixo disso não lê como troca de plano, lê como erro de render.

Cada etapa narrativa tem um `zoom_base` que multiplica a escada — fecha onde a
fala é emocional, abre onde é explicativa: Gancho 1,06 · Dor 1,00 · Virada 1,09
· Explicação 1,00 · Revelação 1,08 · Prova 1,03 · Monetização 1,00 · Oferta
1,10 · Garantia 1,06 · CTA 1,12.

**O recorte é concêntrico no rosto, sem pan.** Somar deslocamento aleatório
"para dar variedade" faz o rosto mudar de posição a cada corte e o olho cansa
perseguindo. Só a escala muda — é o que uma segunda câmera faria. O centro do
rosto é medido sozinho: sem detector de rosto e sem dependência pesada, o
editor amostra pares de quadros vizinhos ao longo do vídeo e acha o centro do
**movimento** (a boca abre e fecha; o fundo fica parado), pela mediana das
amostras. Medido em três posições conhecidas: erro de 2 a 3% do quadro. Se
você tiver OpenCV instalado, o haarcascade entra na frente. Dá para corrigir na
mão em **Rosto X / Rosto Y**.

**O teto depende da resolução da fonte.** Recortar 1,20x de um vídeo de 1080 de
largura é pegar 900 px e esticar de volta — perde nitidez. O editor nunca
estica mais que 15% acima do que a fonte entrega: fonte 1080 para saída 1080 dá
teto 1,15x; fonte 4K para saída 1080 dá 1,25x. Quando o preset pede mais que
isso, a escada é reduzida **proporcionalmente** em vez de cortada no teto —
cortar faria vários degraus virarem o mesmo valor e a troca sumiria.

Um resíduo é irredutível e vale saber: um ponto a D pixels da âncora anda
`D × (zoom_maior − zoom_menor)` pixels entre o plano mais aberto e o mais
fechado. Medido: rosto a 0,42 da altura, âncora forçada para 0,472 pela
geometria do zoom 1,06 — 9 px em 1920, **0,47% da altura**. É o mínimo que a
matemática permite.

Tudo entra no mesmo `filter_complex` do bloco, junto com a velocidade. Cada
bloco continua sendo encodado **uma única vez**.

### Camadas

A trilha tem faixas separadas, e cada uma aceita itens:

| trilho | o que entra |
|---|---|
| **Vídeo** | o take principal, já cortado |
| **Sobreposição** | vídeo ou imagem por cima, por tempo determinado |
| **Desfoque** | proteção de rosto e documento |
| **Trilha** | música de fundo, com ducking automático na fala |

Arraste um item para movê-lo, arraste a borda para mudar a duração,
**Shift+clique** apaga. Os botões **+ sobreposição** e **+ trilha** na barra da
timeline levam para a aba onde se importa o arquivo.

**A agulha** atravessa todas as camadas. Pegue nela e arraste para qualquer
lugar; o **▶ tocar** fica na própria barra da timeline, ao lado do zoom.

**Por que ele não confunde palma com palavra forte.** Pico, salto de volume,
duração e ataque a partir do silêncio — os quatro critérios de envelope — não
separam nada: uma palavra enfática logo depois de uma pausa passa em todos, e
foi isso que enchia a timeline de marcas erradas. O que separa é o timbre, e o
editor mede três coisas:

| | palma | palavra forte |
|---|---|---|
| tempo de subida (10→90%) | 0,1 a 1,5 ms | 43 a 325 ms |
| planura espectral (ruído x harmônico) | 0,84 a 0,86 | 0,05 a 0,08 |
| agudo sobre grave | 4,2 a 6,3 | 0,03 a 0,39 |

Som que falha em dois desses três nem entra na lista. O resto é palma e
descarta o take **sozinho** — o editor nunca pergunta “isso foi palma?”.
Se ele errar, a bandeirinha laranja na timeline desliga com um clique, ou você
usa **voltar** em “Saiu sozinho”.

### Exportar

### Onde o vídeo pronto aparece

No topo da aba **Exportar** está escrito, em verde, a pasta de saída — e ela é
uma pasta que uma pessoa acha:

| sistema | pasta |
|---|---|
| Windows | `C:\Users\<você>\Vídeos\Editor de Vídeo\` |
| macOS | `~/Movies/Editor de Vídeo/` |
| Linux | `~/Vídeos/Editor de Vídeo/` |

O botão **📁 abrir a pasta** abre o Explorer (ou o Finder) já com o arquivo
selecionado. **trocar** muda a pasta para onde você quiser.

Junto do `.mp4` saem o `.srt` e o `.ass` com o mesmo nome. Exportar de novo
**não sobrescreve**: vira `nome (2).mp4`.

Aba **Exportar** → **EXPORTAR**. Antes disso vale clicar em **estimar** para ver
o bitrate que vai sair comparado com o do original; se cair mais de 40%, aparece
um aviso em destaque e você baixa o CRF.

Depois de exportar, clique em **validar**. O editor transcreve o resultado,
compara palavra a palavra com o esperado, mede a sincronia das legendas contra o
áudio real, confere LUFS, pico, clipping, bitrate e duração — e diz se dá para
publicar sem assistir o vídeo inteiro.

---

## 6. A GPU está sendo usada?

Na tela inicial, lá embaixo, aparece uma linha assim:

```
transcrição: faster-whisper · modelo large-v3 · cuda/float16 — 1 GPU(s) CUDA visível(is)

```

- Se aparecer **`cuda/float16`** → está usando a GPU. 
- Se aparecer **`cpu/int8`** → está usando o processador.

O mesmo aparece na janela preta quando o programa abre.

### Como fazer a GPU funcionar (placa NVIDIA)

Você precisa de uma placa NVIDIA e das bibliotecas CUDA. No PowerShell:

```powershell
.venv\Scripts\pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

Feche o editor e abra de novo. Se continuar em `cpu`, confira se a placa aparece:

```powershell
nvidia-smi
```

Se esse comando não existir, instale o driver mais novo em
<https://www.nvidia.com/Download/index.aspx> e reinicie o computador.

> **Placa AMD ou Intel, ou Mac?** A transcrição roda na CPU e funciona — só
> demora mais. Veja a seção 7. A *exportação* de vídeo pode usar a GPU mesmo
> assim: na aba Exportar, em **Encoder**, escolha **GPU automático**.

### GPU na exportação

A tela inicial também lista os encoders de vídeo por GPU que a sua máquina tem
(`h264_nvenc`, `h264_qsv`, `h264_videotoolbox`…). Na aba **Exportar**, em
**Encoder**, escolha **GPU automático** para exportar bem mais rápido.

> A qualidade do encoder por GPU é um pouco inferior à da CPU no mesmo tamanho de
> arquivo. Para o criativo final, prefira **CPU (libx264)** com CRF 15. Para
> testar rápido, use a GPU.

---

## 7. A transcrição está lenta

O editor escolhe o modelo sozinho pelo seu hardware: **`large-v3`** (o mais
preciso) quando encontra uma GPU NVIDIA, e **`turbo`** quando vai rodar na CPU.
O `turbo` tem quase a mesma precisão e é várias vezes mais rápido — numa CPU o
`large-v3` levaria de 4 a 8 minutos para cada minuto de vídeo, e você acharia
que travou.

Mesmo com o `turbo`, uma CPU leva algo como 1 a 2 minutos para cada minuto de
vídeo. Numa GPU decente são uns 15 segundos por minuto.

**Da mais rápida para a mais precisa, escolha uma:**

### a) Trocar o modelo

Feche o editor. No PowerShell, dentro da pasta do projeto:

```powershell
$env:EDITOR_WHISPER_MODEL="turbo"
.venv\Scripts\python -m editor
```

Modelos, do mais rápido ao mais preciso:

| Modelo | Velocidade | Precisão | Espaço em disco |
|---|---|---|---|
| `tiny` | muito rápido | fraca | 75 MB |
| `base` | rápido | fraca | 145 MB |
| `small` | médio | boa | 490 MB |
| `medium` | lento | muito boa | 1,5 GB |
| `turbo` | **rápido** | **muito boa** | 1,6 GB |
| `large-v3` | lento | a melhor | 3,1 GB |

**`turbo` é o melhor negócio** para quem não tem GPU: quase a precisão do
`large-v3` numa fração do tempo.

Para deixar essa escolha permanente, edite o arquivo `iniciar.bat` e acrescente
a linha `set EDITOR_WHISPER_MODEL=turbo` antes da linha que chama o Python.

### b) Ligar a GPU

Veja a seção 6. É o que mais muda o tempo.

### c) O que NÃO adianta

- Fechar o navegador: a transcrição roda no programa, não na página.
- Deixar o vídeo menor: o que pesa é o áudio, não a imagem.

### d) A primeira vez é sempre mais lenta

Na primeira transcrição o modelo é baixado da internet (1,5 GB no caso do
`turbo`). Da segunda em diante ele já está no seu disco e começa na hora.

---

## 8. Onde ficam os arquivos

O editor guarda o trabalho dele numa pasta separada, **sem tocar nos seus
vídeos**:

- **Windows:** `C:\Users\SeuNome\AppData\Local\editor-de-video`
- **macOS:** `~/Library/Application Support/editor-de-video`

Dentro dela:

| Pasta | O que tem |
|---|---|
| `editor.sqlite3` | os projetos, presets e o dicionário de correções |
| `projects\<id>\audio16k.wav` | o áudio extraído para análise |
| `projects\<id>\work\` | os trechos já encodados (é o que permite retomar) |
| `projects\<id>\exports\` | **os vídeos exportados** |

Os vídeos prontos ficam em `exports`. O botão **baixar / abrir** na aba Exportar
leva direto a eles.

Para liberar espaço, pode apagar as pastas `work` — só faz a próxima exportação
recomeçar do zero.

---

## 9. Problemas comuns

**Antes de qualquer coisa: rode o autoteste**
`.venv\Scripts\python -m editor --test`. Ele diz em qual etapa a sua máquina
trava, e a mensagem dele é bem mais específica do que “não funcionou”.

**“ffmpeg não encontrado” quando abro o programa**
O ffmpeg não está instalado ou o Windows não está enxergando. Volte à seção 2.2.
O jeito mais garantido é colocar os arquivos em `C:\ffmpeg\bin\` — o editor
procura ali sozinho.

**A página abre em branco**
A interface já vem compilada no projeto, então isso não deveria acontecer.
Se você mexeu no código do frontend, recompile: entre na pasta `frontend` e rode
`npm install` e depois `npm run build`. Fora esse caso, **você não precisa de
Node.js para nada**.

**“faster-whisper não está instalado”**
Rode `.venv\Scripts\pip install faster-whisper`.

**O editor abre mas o vídeo não toca na prévia**
O navegador não sabe tocar o formato do seu arquivo (acontece com HEVC/H.265 e
com alguns .mkv). A edição e a exportação funcionam do mesmo jeito — só a prévia
fica sem imagem. Se quiser prévia, exporte uma versão em 480p (aba Exportar →
Resolução → 480) e assista a ela.

**Arrastei o vídeo e ele disse que não achou**
O navegador não entrega o caminho de um arquivo arrastado, então o editor procura
pelo nome nas pastas comuns. Se o vídeo está num HD externo ou numa pasta
incomum, use **Escolher no disco**, ou cole o caminho completo no campo de texto.

**Aparece “a borda de corte está cortando fala”**
É a auditoria funcionando. Clique em **corrigir com um clique** no painel da
direita. Se aparecer muito, o vídeo provavelmente tem pouco silêncio entre as
frases — aumente o valor de “silêncio mínimo” no preset.

**O vídeo exportado ficou com menos qualidade**
Confira o aviso de bitrate na aba Exportar. CRF menor = melhor qualidade e
arquivo maior. O padrão é 15, que é bem alto. Se você usou o encoder por GPU,
volte para CPU.

**Quero começar tudo de novo**
Apague a pasta da seção 8. Você perde os projetos, mas seus vídeos originais
nunca foram tocados.

---

## 10. Como funciona por dentro

Não é necessário ler isto para usar o editor. Está aqui porque explica as três
decisões que fazem a diferença.

### O corte não come palavra

O Whisper erra ±80 ms nas bordas das palavras. Cortar pelo timestamp dele decepa
o ataque das consoantes: “câmera” vira “canto”, “chocolate” vira “chocou”.

Por isso quem decide onde cortar aqui **não é o Whisper, é o envelope de
energia** do áudio, medido em janelas de 10 ms. Toda borda de corte é empurrada
para o vale de energia mais próximo. O piso de ruído é calculado por gravação
(percentil 2 do envelope) e todos os limiares saem dele — nada de valor fixo.

A busca da borda de saída vai até 0,95 s à frente de propósito, porque o Whisper
fecha o token antes do fim real da fala com frequência.

Se depois de tudo uma borda ainda cair em cima de fala, ela é resgatada para o
meio do vale mais próximo dentro do intervalo removido. Se o intervalo inteiro
for fala — ou seja, a pausa que a transcrição prometeu não existe no áudio — **o
corte não acontece**. O envelope ganha do Whisper.

E dois blocos só podem ser fundidos quando são contíguos na fonte. É essa regra
que impede o bug em que um trecho removido volta sozinho porque um bloco curto
foi fundido com o vizinho estendendo o fim por cima do corte.

### Uma geração de encode

O plano de edição é uma estrutura de dados, não um arquivo. **Nenhuma alteração
renderiza nada.** Você pode cortar, acelerar, legendar e mudar de ideia cem vezes
sem tocar em um pixel.

Só a exportação encoda, e cada trecho é encodado **uma única vez, direto do
arquivo original**. Os trechos saem com parâmetros idênticos e são juntados sem
reencodar (`-c copy`). O áudio é montado em PCM, passa pela cadeia de tratamento
uma vez só e vira AAC apenas no mux final.

É isso que evita o que acontece quando se aplica cada alteração por cima do
arquivo já encodado: três alterações viram três gerações de H.264 e o bitrate
despenca.

### Sincronia sem deriva

Blocos de vídeo são quantizados em quadros; áudio não. Ao longo de dezenas de
blocos isso acumula.

A conta `duração ÷ velocidade` erra alguns milissegundos por bloco e a soma
desses erros vira quase um segundo em 36 blocos. Aqui a linha do tempo é montada
somando as **durações reais medidas** de cada trecho renderizado, e o áudio de
cada bloco é gerado já com o número exato de amostras que aquele bloco de vídeo
pede.

Nos testes de ponta a ponta deste projeto a deriva entre vídeo e áudio dá
**0,0 ms**, e o desvio das legendas fica em **26 ms na mediana — com 30,6 ms na
primeira metade do vídeo contra 30,4 ms na segunda**, ou seja, sem acúmulo.
Se ainda sobrar diferença, ela é corrigida reescalando os timestamps do vídeo
(`-itsscale`), que não reencoda nada e é invisível.

---

## Ajustes por variável de ambiente

Para quem quiser mexer. No Windows, no PowerShell, antes de abrir o editor:

| Variável | O que faz | Padrão |
|---|---|---|
| `EDITOR_WHISPER_MODEL` | modelo de transcrição | `auto` (large-v3 na GPU, turbo na CPU) |
| `EDITOR_WHISPER_LANGUAGE` | idioma | `pt` |
| `EDITOR_WHISPER_DEVICE` | `auto`, `cuda` ou `cpu` | `auto` |
| `EDITOR_PORT` | porta do servidor | `8000` |
| `EDITOR_DATA_DIR` | onde guardar projetos e exportações | ver seção 8 |
| `EDITOR_FFMPEG` | caminho completo do `ffmpeg.exe` | procura sozinho |

Exemplo:

```powershell
$env:EDITOR_WHISPER_MODEL="turbo"
$env:EDITOR_PORT="8080"
.venv\Scripts\python -m editor
```


---

## 12. A aba IA

Opcional. Desligada por padrão. Sem ela o editor faz tudo o que fazia.

### A IA decide os cortes — automática

Com a chave colada, **a IA entra no EDITAR sozinha**: assim que a transcrição
sai, ela lê a fala inteira — com as palmas e assobios marcados no lugar onde
aconteceram — e decide **quais trechos saem**: tentativa refeita (fica a última
versão), falso começo, contagem de gravação, muleta solta. A resposta volta em
**faixas de palavras**, nunca em tempos: quem encosta a borda no vale de
energia continua sendo o programa, então a regra "corte não come palavra" fica
onde sempre esteve.

Cada decisão vira um item em **"Saiu sozinho"**, com o texto riscado, o motivo
que a IA deu e o botão de **voltar**. Resposta ruim é barrada: faixa que não
existe é recusada, e uma resposta que quisesse remover mais de 85% do vídeo é
descartada inteira. Sem internet, sem chave ou com cota estourada, a regra
determinística decide sozinha — a análise nunca trava por causa da IA.

O modelo preferido é o **gemini-3.5-flash** (a lista real da sua chave manda;
se ele não existir, o app cai para o flash mais próximo). A chave é colada
**uma vez** e fica guardada no banco local — fora da pasta do programa, então
sobrevive a atualização e reinstalação. Quem preferir não ter chave em disco
usa a variável de ambiente `EDITOR_GEMINI_KEY` no `iniciar.bat`.

### A IA também corta a COPY

Além de achar a tentativa refeita, a IA lê o que você falou como **diretor de
criação** e tira o que atrapalha: redundância (já disse com outras palavras),
preâmbulo ("então, olha, deixa eu te falar"), auto-comentário ("não sei se
ficou claro"), divagação que não volta, e final duplo.

**Mas quem decide se dá para cortar é o áudio, não a IA.** Medido antes de
escrever a regra: dentro de uma frase corrida, 4 de 5 pontos não têm vale
nenhum onde esconder a emenda, e a costura salta até 6 dB. Na fronteira de
frase o vale tem 250 ms e o salto é 0,0 dB. Então a regra não é "palavra ×
frase" — é **tem respiro ou não tem**:

| | tem vale? | emenda |
|---|---|---|
| 1 palavra no meio da frase | 4 de 5 pontos: nenhum | até 6,1 dB |
| frase inteira na pausa | 250 ms | 0,0 dB |
| muleta cercada de micro-pausa | 210 ms | 0,0 dB |

Toda proposta de corte de copy passa por esse veto. Sem vale nas duas bordas,
ela é **recusada com o motivo na tela** ("não tem respiro aqui — cortar no
meio da fala corrida sai picotado").

Mais três travas: o **gancho é intocável** (os primeiros 8 s, ou 15% do vídeo
se ele for curto), o copy não passa de **25% do vídeo** (o resto é só o que
você mandou), e cada emenda de copy **força troca de enquadramento** — porque
o som emenda perfeito mas a imagem pula, e é jump cut de cabeça falante.

Tudo isso aparece em **"Saiu sozinho"** com o texto e o motivo, e um clique
traz de volta.

### Qual modelo usar

A diferença de preço entre os modelos é de **centavos por vídeo** (um vídeo de
2 min custa R$ 0,018 no Flash contra R$ 0,053 no Pro), então escolher por
preço é bobagem. Na aba IA, o botão **Comparar modelos neste vídeo** roda os
dois no *seu* vídeo e mostra lado a lado o que cada um quis cortar, com o
motivo. Você decide olhando.

### O botão "Ler o roteiro" (opcional, além dos cortes)

A IA **opina**, o programa **executa**. Ela lê o texto do que você falou,
bloco a bloco, e responde três coisas:

1. **Em que etapa cada bloco está** — gancho, dor, mecanismo, explicação,
   revelação, prova, monetização, oferta, garantia, CTA.
2. **Onde o ritmo pede um plano mais fechado** — o pico da argumentação fecha,
   o respiro abre, e a alternância é o que dá dinâmica.
3. **Onde cada anexo seu entra** e por quanto tempo. **Toda mídia que você
   anexou na primeira tela sai no vídeo, exatamente uma vez** — você anexou
   de propósito. A IA diz o bloco; o que ela deixar de fora (ou errar) o
   programa posiciona sozinho: pelas palavras da sua descrição contra a fala
   e, na falta delas, espalhado no meio do vídeo, nunca no gancho. Isso vale
   até **sem chave da IA** — só o cartão de tópico precisa dela, porque é ela
   quem escreve as palavras. Depois, em cima da própria prévia, você
   **arrasta** a sobreposição, **redimensiona** pelo canto e **apaga** com
   Delete; uma cobertura vira um chip com × no canto do vídeo.

O que ela **não** faz: escolher tempo de corte, valor de zoom, posição em
pixels, ou mexer em qualquer coisa sozinha. Ela responde por *índice de bloco*
justamente para não precisar acertar um instante — um índice sempre cai numa
fronteira de bloco, que é onde um anexo pode entrar sem partir frase no meio.

Todo o resto é o mesmo maquinário determinístico de sempre. A etapa vira
enquadramento pela tabela `SECTIONS`, com as mesmas seis invariantes de
`editor/edit/zoom.py` (troca só em corte, cena mínima de 2 s, passo mínimo de
0,05, teto pela resolução da fonte, âncora alcançável, bloco travado
intocado). O anexo passa inteiro por `editor/anexos.py`. **Se a sugestão não
couber, ela é recusada com o motivo escrito na tela** — nunca aplicada pela
metade — e a mídia recusada entra assim mesmo, pela regra do programa.

E o que a automação tirou você resolve **clicando no vermelho** da linha do
tempo: o corte fica selecionado, Delete devolve o trecho, e os botões
**◂ respira** / **cola ▸** afastam ou colam o corte na fala em passos de 80 ms
— o mesmo vocabulário do controle de corte, agora por trecho.

Um bloco que **você** travou é intocável para a IA, e isso aparece na lista de
recusas.

### O que sai da sua máquina

| | sai? |
|---|---|
| o arquivo de vídeo | **não** |
| o caminho do arquivo | **não** |
| qualquer segundo de imagem sua | **não** |
| o texto transcrito, em blocos | sim |
| um quadro de 360 px de cada **anexo** seu | só se você marcar a caixa |

### O aviso que não dá para esconder

No **plano gratuito** do Gemini, os termos da Google dizem que ela usa o que
entra e o que sai para melhorar os produtos dela, e que **revisores humanos
podem ler e anotar** essa entrada e essa saída. Só no plano **pago**
(faturamento ligado na conta Google) ela garante que não usa seus prompts nem
suas respostas para melhorar produtos.

Autorizar uma IA a ver não é a mesma coisa que autorizar um revisor humano a
ler. A escolha é sua — mas ela está escrita aqui e está escrita na tela, antes
do botão.

### Como ligar

1. Pegue uma chave no Google AI Studio.
2. **Na tela inicial**, no card do topo: cole a chave → **guardar**. Ela é
   testada na hora, então um erro de chave aparece ali, com o campo na frente
   — e não no meio do processamento de um vídeo de 2 GB.
3. Pronto. Todo vídeo que você soltar dali em diante já sai cortado pela IA.
   O campo de modelo (aba IA) pode ficar em branco: o programa pergunta ao
   Google quais modelos aquela chave alcança e escolhe. Nome de modelo
   chumbado no código é um app que quebra sozinho — os identificadores do
   Gemini mudaram quatro vezes em menos de dois anos.

**Esqueceu de colar antes de soltar o vídeo?** Sem problema. A etapa da IA
aparece na tela de processamento dizendo *"sem chave do Gemini: corte pela
regra do programa"*, e o editor abre com uma faixa amarela oferecendo o
conserto. Cole a chave e aperte **refazer edição**: a IA roda em cima da
transcrição que já existe, **sem transcrever o vídeo de novo**.

A chave fica guardada no banco local do programa. Ela **nunca** volta por
nenhuma rota da API — só um "tem chave: sim" e os quatro últimos caracteres,
o bastante para você reconhecer qual é. Isso importa porque o
`iniciar-rede.bat` existe justamente para você revisar do celular, e aí
qualquer um na sua rede local alcança as rotas.

Sem internet, a aba IA avisa e o resto do editor continua igual.
