# Editor de Vídeo

Você joga o vídeo bruto na página, escolhe um preset, aperta **EDITAR** e recebe
o vídeo cortado, acelerado e legendado. Depois revisa e ajusta o que ficou fora
do lugar.

**O arquivo nunca sai da sua máquina.** Não existe upload, não existe nuvem, não
existe limite de tamanho. O programa roda no seu computador e lê o vídeo direto
da pasta onde ele já está.

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

---

## 1. O que você precisa instalar

Três coisas. Uma vez só, na primeira vez.

| O quê | Para quê | Tamanho |
|---|---|---|
| **Python 3.10 ou mais novo** | é a linguagem em que o editor foi escrito | ~30 MB |
| **ffmpeg** | é quem realmente corta, acelera e encoda o vídeo | ~150 MB |
| **O modelo de transcrição** | é quem escuta o áudio e escreve as palavras | ~1,5 GB (baixa sozinho na primeira vez) |

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
   `C:\Users\SeuNome\editor-de-video`.
2. Abra a pasta no Explorador de Arquivos.
3. Clique com o botão direito num espaço vazio → **Abrir no Terminal**
   (ou **Abrir janela do PowerShell aqui**).
4. Dê um duplo clique em **`instalar.bat`** — ou, no terminal, cole:

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

## 4. Abrir o editor

**Windows:** duplo clique em **`iniciar.bat`**.
**macOS:** duplo clique em **`iniciar.command`**.

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
 transcrição ..... OK  (faster-whisper, cuda/float16)
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

### Revisar

- **Aba Texto** — a transcrição inteira. Clique numa palavra, Shift+clique para
  estender a seleção, **Delete** para tirar aquele trecho do vídeo. O texto
  removido fica riscado (dá para recuperar). Muletas como “simplesmente”,
  “então”, “né” vêm sublinhadas: pontilhado amarelo = dá para tirar; ondulado
  vermelho = tirar quebra a palavra vizinha, é melhor manter.
- **Timeline** — arraste sobre a forma de onda para selecionar e aperte
  **Delete**. Clique num bloco colorido para mudar a velocidade ou a seção.
  A roda do mouse dá zoom (de 1 segundo até o vídeo inteiro), Alt+arraste move.
- **Painel da direita** — alertas de borda (“esta borda está cortando fala”),
  com correção de um clique. Palmas suspeitas para você confirmar.
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

### O protocolo da palma

Se você errar uma frase durante a gravação: **bata uma palma, conte até três e
refaça a frase inteira.** O editor detecta a palma e descarta sozinho a frase que
estava em andamento (não tudo que veio antes — só a tentativa que deu errado).

Os takes descartados aparecem em cinza na timeline. Se o take novo tiver saído
pior que o antigo, passe o mouse por cima e clique em **recuperar este take**.

Uma palma que não vem do silêncio (o critério que separa palma de sílaba tônica)
aparece em **amarelo** e nunca é descartada sozinha — o painel da direita pede
sua confirmação.

### Exportar

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

O modelo padrão é o `large-v3`, que é o mais preciso e o mais pesado. Numa CPU
comum ele leva de 4 a 8 minutos para cada minuto de vídeo. Numa GPU decente, uns
15 segundos por minuto de vídeo.

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
| `EDITOR_WHISPER_MODEL` | modelo de transcrição | `large-v3` |
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
