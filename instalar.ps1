<#
    Instalador do Sharkcut — uma janela de verdade, não uma tela preta.

    Por que PowerShell e não um instalador compilado (Inno Setup, NSIS):
    esses precisam ser COMPILADOS num Windows antes de virar .exe, e aí o
    programa passaria a ter um passo de build que ninguém aqui roda. O
    PowerShell com WinForms já vem em qualquer Windows desde o 7, desenha
    janela nativa, e o arquivo continua sendo texto que dá para ler e
    corrigir. Zero dependência nova.

    O que ele faz, na ordem: confere Python, espaço em disco e ffmpeg, cria
    (ou confere) o ambiente compartilhado, instala as bibliotecas mostrando
    a saída ao vivo, roda a conferência e cria os atalhos com o ícone do
    tubarão.

    Nada aqui pede administrador: tudo mora no perfil do usuário
    (%LOCALAPPDATA%, Área de Trabalho, Menu Iniciar). Instalador que pede
    UAC sem precisar é instalador que assusta.

    Se o WinForms não subir, o script sai com código 2 e o instalar.bat cai
    sozinho para o instalador de console, que continua ali inteiro.
#>

# NÃO declaramos a janela como "ciente de DPI" de propósito. Numa tela a
# 125% (o caso de quase todo notebook), o Windows passaria a desenhar a fonte
# 25% maior enquanto as posições daqui continuam medidas em pixel fixo — o
# texto estoura a caixa e o instalador fica quebrado. Deixando o Windows
# esticar a janela inteira, tudo cresce junto: fica um fio menos nítido numa
# tela 4K e continua inteiro em qualquer escala, que é o que importa num
# programa que roda uma vez na vida.

try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    Add-Type -AssemblyName System.Drawing -ErrorAction Stop
} catch {
    exit 2
}
[System.Windows.Forms.Application]::EnableVisualStyles()

# ------------------------------------------------------------------ caminhos
$dir     = $PSScriptRoot
if (-not $dir) { $dir = Split-Path -Parent $MyInvocation.MyCommand.Definition }
$ico     = Join-Path $dir 'sharkcut.ico'
$req     = Join-Path $dir 'requirements.txt'
$iniciar = Join-Path $dir 'iniciar.bat'
# O NOME DA PASTA DO AMBIENTE NÃO MUDA COM O NOME DO PROGRAMA. Ela está
# gravada nas instalações que já existem; trocar aqui faria todo mundo
# baixar 3 GB de novo por causa de um rótulo.
$venv    = Join-Path $env:LOCALAPPDATA 'Editor de Video\venv'
$pyVenv  = Join-Path $venv 'Scripts\python.exe'

# ------------------------------------------------------------------- paleta
$corFundo   = [System.Drawing.Color]::FromArgb(10, 12, 16)
$corPainel  = [System.Drawing.Color]::FromArgb(15, 18, 24)
$corLinha   = [System.Drawing.Color]::FromArgb(42, 50, 67)
$corTexto   = [System.Drawing.Color]::FromArgb(230, 237, 247)
$corFraco   = [System.Drawing.Color]::FromArgb(148, 163, 184)
$corMarca   = [System.Drawing.Color]::FromArgb(56, 189, 248)
$corOk      = [System.Drawing.Color]::FromArgb(74, 222, 128)
$corErro    = [System.Drawing.Color]::FromArgb(248, 113, 113)
$corSecao   = [System.Drawing.Color]::FromArgb(100, 116, 139)
$corTrilho  = [System.Drawing.Color]::FromArgb(28, 34, 48)

function Fonte($tamanho, $estilo = [System.Drawing.FontStyle]::Regular) {
    New-Object System.Drawing.Font('Segoe UI', $tamanho, $estilo)
}

# -------------------------------------------------------------------- janela
#
# O desenho foi feito antes num molde em HTML do mesmo tamanho da janela, só
# para poder OLHAR o resultado antes de escrever — WinForms não roda na
# máquina onde este arquivo foi escrito. As medidas abaixo saíram desse molde.
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Instalar o Sharkcut'
$form.ClientSize = New-Object System.Drawing.Size(620, 692)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.BackColor = $corFundo
$form.ForeColor = $corTexto
$form.Font = Fonte 9
try { $form.Icon = New-Object System.Drawing.Icon($ico) } catch { }

function NovoPainel($pai, $x, $y, $largura, $altura, $cor) {
    $p = New-Object System.Windows.Forms.Panel
    $p.SetBounds($x, $y, $largura, $altura)
    $p.BackColor = $cor
    $pai.Controls.Add($p)
    return $p
}

# Rótulo com altura de LINHA e texto centrado na vertical. Sem o TextAlign o
# texto cola no topo da caixa e as linhas do cartão saem desencontradas.
function NovoRotulo($pai, $x, $y, $largura, $altura, $texto, $cor, $fonte) {
    $l = New-Object System.Windows.Forms.Label
    $l.SetBounds($x, $y, $largura, $altura)
    $l.Text = $texto
    $l.ForeColor = $cor
    $l.TextAlign = 'MiddleLeft'
    if ($fonte) { $l.Font = $fonte }
    $pai.Controls.Add($l)
    return $l
}

# ---------------------------------------------------------------- cabeçalho
$topo = NovoPainel $form 0 0 620 96 $corPainel
# a linha fica ABAIXO do painel, não em cima: no WinForms quem é
# adicionado depois vai para trás, então dentro do painel ela sumiria
$null = NovoPainel $form 0 96 620 1 $corLinha

$logo = New-Object System.Windows.Forms.PictureBox
$logo.SetBounds(28, 20, 56, 56)
$logo.SizeMode = 'Zoom'
$logo.BackColor = $corPainel
# UM PNG, NÃO O .ico. O Icon.ToBitmap() do .NET não sabe ler entrada de ícone
# comprimida em PNG — que é o formato do nosso .ico inteiro — e acaba
# desenhando os bytes do PNG como se fossem pixels: sai um chuvisco colorido
# no lugar da marca. Foi exatamente o que apareceu na tela do usuário.
try { $logo.Image = [System.Drawing.Image]::FromFile((Join-Path $dir 'sharkcut-logo.png')) } catch { }
$topo.Controls.Add($logo)

$null = NovoRotulo $topo 100 20 400 34 'Sharkcut' $corTexto (Fonte 18 ([System.Drawing.FontStyle]::Bold))
$null = NovoRotulo $topo 102 54 460 20 'editor de vídeo local — nada sai da sua máquina' $corFraco $null

# ------------------------------------------------------------ o que precisa
$fonteSecao = Fonte 8
$null = NovoRotulo $form 28 114 300 16 'O QUE PRECISA' $corSecao $fonteSecao
$cartaoChecagem = NovoPainel $form 28 136 564 100 $corPainel
$lblPython = NovoRotulo $cartaoChecagem 16 8 532 28 '•  Python' $corFraco $null
$lblDisco  = NovoRotulo $cartaoChecagem 16 36 532 28 '•  espaço em disco' $corFraco $null
$lblFfmpeg = NovoRotulo $cartaoChecagem 16 64 532 28 '•  ffmpeg' $corFraco $null

# --------------------------------------------------------- quando terminar
$null = NovoRotulo $form 28 254 300 16 'QUANDO TERMINAR' $corSecao $fonteSecao
$cartaoOpcoes = NovoPainel $form 28 276 564 124 $corPainel

function NovaCaixa($pai, $y, $texto, $marcada) {
    $c = New-Object System.Windows.Forms.CheckBox
    $c.SetBounds(16, $y, 532, 28)
    $c.Text = $texto
    $c.Checked = $marcada
    $c.ForeColor = $corTexto
    $c.BackColor = $corPainel
    $c.FlatStyle = 'Flat'
    $c.TextAlign = 'MiddleLeft'
    $pai.Controls.Add($c)
    return $c
}

$cxArea  = NovaCaixa $cartaoOpcoes 8  'Criar o ícone na Área de Trabalho' $true
$cxMenu  = NovaCaixa $cartaoOpcoes 36 'Criar o ícone no Menu Iniciar' $true
$cxBarra = NovaCaixa $cartaoOpcoes 64 'Fixar na barra de tarefas (pode pedir um clique seu)' $true
$cxAbrir = NovaCaixa $cartaoOpcoes 92 'Abrir o Sharkcut assim que terminar' $true

# -------------------------------------------------------------------- andamento
# Barra desenhada à mão: um trilho e um preenchimento, dois painéis. A
# ProgressBar do Windows vem com o verde do sistema e um brilho animado que
# não dá para desligar — fica com cara de instalador de 2009 dentro de uma
# janela escura.
$trilho = NovoPainel $form 28 420 564 6 $corTrilho
$cheio = New-Object System.Windows.Forms.Panel
$cheio.SetBounds(0, 0, 0, 6)
$cheio.BackColor = $corMarca
$trilho.Controls.Add($cheio)

$lblStatus = NovoRotulo $form 28 434 564 22 'Pronto para instalar.' $corMarca $null

$null = NovoRotulo $form 28 464 300 16 'DETALHES' $corSecao $fonteSecao
$log = New-Object System.Windows.Forms.TextBox
$log.SetBounds(28, 486, 564, 118)
$log.Multiline = $true
$log.ReadOnly = $true
$log.ScrollBars = 'Vertical'
$log.BackColor = [System.Drawing.Color]::FromArgb(11, 15, 20)
$log.ForeColor = $corFraco
$log.Font = New-Object System.Drawing.Font('Consolas', 8)
$log.BorderStyle = 'FixedSingle'
$form.Controls.Add($log)

# ----------------------------------------------------------------------- botões
$btSair = New-Object System.Windows.Forms.Button
$btSair.SetBounds(28, 624, 110, 42)
$btSair.Text = 'Sair'
$btSair.FlatStyle = 'Flat'
$btSair.BackColor = $corPainel
$btSair.ForeColor = $corTexto
$btSair.FlatAppearance.BorderColor = $corLinha
$btSair.Add_Click({ $form.Close() })
$form.Controls.Add($btSair)

$btInstalar = New-Object System.Windows.Forms.Button
$btInstalar.SetBounds(372, 624, 220, 42)
$btInstalar.Text = 'INSTALAR'
$btInstalar.FlatStyle = 'Flat'
$btInstalar.BackColor = $corMarca
$btInstalar.ForeColor = $corFundo
$btInstalar.Font = Fonte 10 ([System.Drawing.FontStyle]::Bold)
$btInstalar.FlatAppearance.BorderSize = 0
$form.Controls.Add($btInstalar)

# ------------------------------------------------------------------ auxílios
$script:arquivoLog = Join-Path $env:TEMP 'sharkcut-instalacao.log'

function Escrever($texto) {
    if (-not $texto) { return }
    $limpo = $texto.TrimEnd()
    $log.AppendText(($limpo + "`r`n"))
    # a caixa de log mostra umas dez linhas; o arquivo guarda tudo, que é o
    # que dá para mandar para alguém quando a instalação falha
    try { Add-Content -Path $script:arquivoLog -Value $limpo -Encoding UTF8 } catch { }
    [System.Windows.Forms.Application]::DoEvents()
}

function Status($texto, $cor = $null) {
    $lblStatus.Text = $texto
    if ($cor) { $lblStatus.ForeColor = $cor } else { $lblStatus.ForeColor = $corMarca }
    [System.Windows.Forms.Application]::DoEvents()
}

$PASSOS = 7

function Passo($n) {
    $fracao = [Math]::Min([Math]::Max($n, 0), $PASSOS) / $PASSOS
    $cheio.Width = [int]($trilho.Width * $fracao)
    [System.Windows.Forms.Application]::DoEvents()
}

# Lê o que apareceu no arquivo de log DEPOIS da última leitura. FileShare
# ReadWrite não é detalhe: o processo ainda está escrevendo nele, e sem isso
# a abertura falha com "arquivo em uso".
#
# O tamanho é anotado ANTES da leitura e a posição anda pelo número de bytes
# REALMENTE lidos. A versão que devolvia $fs.Length no fim perdia linha: o
# processo continua escrevendo enquanto a gente lê, então o Length consultado
# depois já era maior do que o que tinha saído — e tudo que entrou nessa
# fresta era pulado para sempre. Num teste de 300 linhas, sumiam 2. Numa
# instalação, poderia sumir justo a linha do erro.
function Derramar($caminho, $de) {
    try {
        $fs = New-Object System.IO.FileStream($caminho, [System.IO.FileMode]::Open,
                  [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try {
            $total = $fs.Length
            if ($total -le $de) { return $de }
            $fs.Position = $de
            $quanto = [int]($total - $de)
            $buffer = New-Object byte[] $quanto
            $lidos = $fs.Read($buffer, 0, $quanto)
            if ($lidos -le 0) { return $de }
            Escrever ([System.Text.Encoding]::UTF8.GetString($buffer, 0, $lidos))
            return ($de + $lidos)
        } finally { $fs.Dispose() }
    } catch { return $de }
}

# Roda um programa mostrando a saída ao vivo, SEM congelar a janela.
#
# A saída vai para arquivo em vez de ser lida direto do processo: ler os dois
# canos (saída e erro) de dentro da mesma linha de execução que desenha a
# janela é o caminho clássico para travar tudo — um cano enche, o processo
# para esperando, e a janela nunca mais responde. Com arquivo, a leitura
# nunca bloqueia.
function Rodar($programa, $argumentos, $titulo) {
    Status $titulo
    $saida = [System.IO.Path]::GetTempFileName()
    $erros = [System.IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath $programa -ArgumentList $argumentos `
                -NoNewWindow -PassThru -RedirectStandardOutput $saida `
                -RedirectStandardError $erros -ErrorAction Stop
    } catch {
        Escrever "não consegui executar: $programa"
        Escrever $_.Exception.Message
        return 1
    }
    $a = 0; $b = 0
    while (-not $p.HasExited) {
        $a = Derramar $saida $a
        $b = Derramar $erros $b
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 150
    }
    $p.WaitForExit()
    $a = Derramar $saida $a
    $b = Derramar $erros $b
    Remove-Item $saida, $erros -Force -ErrorAction SilentlyContinue
    # LER O CÓDIGO DE SAÍDA PODE FALHAR. O objeto que o Start-Process devolve
    # nem sempre mantém o identificador do processo no Windows, e aí a
    # propriedade estoura e o PowerShell entrega $null — que é diferente de
    # zero, então um comando que deu certo passaria por erro. Foi exatamente
    # o que aconteceu: pip dizendo "Requirement already satisfied" em tudo e
    # o instalador anunciando falha. Aqui a leitura é protegida, e quem
    # decide se deu certo é a conferência de verdade, logo abaixo.
    $codigo = -1
    try { $codigo = $p.ExitCode } catch { $codigo = -1 }
    if ($null -eq $codigo) { $codigo = -1 }
    Escrever "(terminou com código $codigo)"
    return [int]$codigo
}

# A VERDADE sobre a instalação não é o código do pip: é se o Python consegue
# importar as bibliotecas. Isso não tem como dar falso positivo nem falso
# negativo — ou o editor tem o que precisa para rodar, ou não tem.
function BibliotecasProntas {
    $codigo = Rodar $pyVenv `
        @('-c', '"import fastapi, uvicorn, numpy, faster_whisper, httpx; print(''bibliotecas ok'')"') `
        'conferindo as bibliotecas'
    return ($codigo -eq 0)
}

# Só a saída, sem janela e sem enfeite — para perguntar a versão de alguém.
function Perguntar($programa, $argumentos) {
    $saida = [System.IO.Path]::GetTempFileName()
    $erros = [System.IO.Path]::GetTempFileName()
    $texto = ''
    try {
        $p = Start-Process -FilePath $programa -ArgumentList $argumentos `
                -NoNewWindow -PassThru -Wait -RedirectStandardOutput $saida `
                -RedirectStandardError $erros -ErrorAction Stop
        $texto = ((Get-Content $saida -Raw -ErrorAction SilentlyContinue) + ' ' +
                  (Get-Content $erros -Raw -ErrorAction SilentlyContinue)).Trim()
    } catch { $texto = '' }
    Remove-Item $saida, $erros -Force -ErrorAction SilentlyContinue
    return $texto
}

function CriarAtalho($destino, $alvo, $descricao) {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($destino)
    $lnk.TargetPath = $alvo
    $lnk.WorkingDirectory = Split-Path -Parent $alvo
    $lnk.Description = $descricao
    if (Test-Path $ico) { $lnk.IconLocation = "$ico,0" }
    $lnk.Save()
}

# Fixar na barra de tarefas por script foi bloqueado pela Microsoft do
# Windows 10 1803 em diante — o verbo simplesmente some do menu. A gente
# TENTA (ainda funciona em máquina mais antiga) e CONFERE se pegou, em vez
# de mentir para o usuário dizendo que fixou.
function FixarNaBarra($lnk) {
    $fixados = Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar'
    $nome = Split-Path -Leaf $lnk
    if (Test-Path (Join-Path $fixados $nome)) { return $true }
    try {
        $sh = New-Object -ComObject Shell.Application
        $item = $sh.Namespace((Split-Path -Parent $lnk)).ParseName($nome)
        foreach ($verbo in $item.Verbs()) {
            $n = ($verbo.Name -replace '&', '')
            if ($n -match 'desafixar|unpin|remover') { continue }
            if ($n -match 'barra de tarefas|taskbar') {
                $verbo.DoIt()
                Start-Sleep -Milliseconds 600
                break
            }
        }
    } catch { }
    return (Test-Path (Join-Path $fixados $nome))
}

# ------------------------------------------------------------------ conferir
function Conferir {
    $ok = $true

    # "where python" acha o ATALHO da Microsoft Store mesmo sem Python
    # nenhum instalado; a única prova é pedir a versão e ler a resposta.
    $script:python = $null
    foreach ($tentativa in @(@('python', @('--version')), @('py', @('-3', '--version')))) {
        $texto = Perguntar $tentativa[0] $tentativa[1]
        if ($texto -match 'Python\s+(\d+\.\d+\.\d+)') {
            $script:python = $tentativa[0]
            $script:pythonArgs = @()
            if ($tentativa[0] -eq 'py') { $script:pythonArgs = @('-3') }
            $lblPython.Text = "✓  Python $($Matches[1])"
            $lblPython.ForeColor = $corOk
            break
        }
    }
    if (-not $script:python) {
        $lblPython.Text = '✗  Python não encontrado — instale marcando "Add python.exe to PATH"'
        $lblPython.ForeColor = $corErro
        $ok = $false
    }

    # Os 6 GB são para instalação DO ZERO (ambiente ~3 GB + modelo de
    # transcrição ~1,5 GB). Com o ambiente já pronto isto é só uma
    # atualização: o pip confere e baixa quase nada.
    $precisa = 6
    if (Test-Path $pyVenv) { $precisa = 1 }
    $livre = 999
    try {
        $unidade = [System.IO.Path]::GetPathRoot($env:LOCALAPPDATA)
        $livre = [int]((New-Object System.IO.DriveInfo($unidade)).AvailableFreeSpace / 1GB)
    } catch { }
    if ($livre -ge $precisa) {
        $lblDisco.Text = "✓  espaço em disco: você tem $livre GB livres (precisa de $precisa)"
        $lblDisco.ForeColor = $corOk
    } else {
        $lblDisco.Text = "✗  espaço em disco: só $livre GB livres, precisa de ~$precisa GB — rode o limpar.bat"
        $lblDisco.ForeColor = $corErro
        $ok = $false
    }

    # ffmpeg pode faltar: não trava a instalação, o instalador tenta pelo
    # winget na hora e, se não der, o editor avisa quando abrir.
    $script:temFfmpeg = $false
    if ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -or
        (Test-Path 'C:\ffmpeg\bin\ffmpeg.exe')) {
        $script:temFfmpeg = $true
        $lblFfmpeg.Text = '✓  ffmpeg'
        $lblFfmpeg.ForeColor = $corOk
    } else {
        $lblFfmpeg.Text = '!  ffmpeg não encontrado — tento instalar pelo winget'
        $lblFfmpeg.ForeColor = $corMarca
    }

    if (-not (Test-Path $req)) {
        Status 'requirements.txt não está aqui: rode este arquivo de dentro da pasta do Sharkcut.' $corErro
        $ok = $false
    }
    return $ok
}

# ----------------------------------------------------------------- instalar
function Instalar {
    $btInstalar.Enabled = $false
    $btInstalar.Text = 'INSTALANDO...'
    $btInstalar.BackColor = $corPainel
    $btInstalar.ForeColor = $corFraco

    if (-not (Conferir)) {
        Status 'Resolva o item em vermelho e abra o instalador de novo.' $corErro
        $btInstalar.Enabled = $true
        $btInstalar.Text = 'INSTALAR'
        $btInstalar.BackColor = $corMarca
        $btInstalar.ForeColor = $corFundo
        return
    }
    Passo 1

    if (-not $script:temFfmpeg) {
        Escrever 'instalando o ffmpeg pelo winget...'
        Rodar 'winget' @('install', '--id', 'Gyan.FFmpeg', '-e',
                         '--accept-source-agreements', '--accept-package-agreements') `
              'instalando o ffmpeg (winget)' | Out-Null
    }
    Passo 2

    if (-not (Test-Path $pyVenv)) {
        Escrever "criando o ambiente em $venv"
        $r = Rodar $script:python ($script:pythonArgs + @('-m', 'venv', ('"{0}"' -f $venv))) `
                   'criando o ambiente (alguns minutos)'
        # Quem diz se o ambiente nasceu é o python.exe existir no lugar certo,
        # não o código de saída — pelo mesmo motivo do passo do pip.
        if (-not (Test-Path $pyVenv)) {
            Escrever "o comando terminou com código $r e o python do ambiente não apareceu"
            Status "Não consegui criar o ambiente. O log inteiro está em $($script:arquivoLog)" $corErro
            $btInstalar.Text = 'FECHAR'
            $btInstalar.Enabled = $true
            $btInstalar.BackColor = $corPainel
            $btInstalar.ForeColor = $corTexto
            $script:terminou = $true
            return
        }
    } else {
        Escrever "ambiente já existe em $venv — só conferindo as bibliotecas"
    }
    Passo 3

    Rodar $pyVenv @('-m', 'pip', 'install', '--upgrade', 'pip') 'atualizando o pip' | Out-Null
    Passo 4

    $r = Rodar $pyVenv @('-m', 'pip', 'install', '-r', ('"{0}"' -f $req)) `
               'instalando as bibliotecas (a parte demorada)'
    if ($r -ne 0) {
        Escrever "o pip terminou com código $r — conferindo se as bibliotecas ficaram prontas assim mesmo"
    }
    if (-not (BibliotecasProntas)) {
        Status "As bibliotecas não ficaram prontas. O log inteiro está em $($script:arquivoLog)" $corErro
        $btInstalar.Text = 'FECHAR'
        $btInstalar.Enabled = $true
        $btInstalar.BackColor = $corPainel
        $btInstalar.ForeColor = $corTexto
        $script:terminou = $true
        return
    }
    Passo 5

    Rodar $pyVenv @('-m', 'editor', '--check') 'conferindo a instalação' | Out-Null
    Passo 6

    # ------------------------------------------------------------- atalhos
    # Um .bat NÃO carrega ícone: o Windows sempre desenha o do cmd.exe. Quem
    # carrega ícone é o ATALHO — e o console aberto por ele herda esse ícone,
    # então a barra de tarefas mostra o tubarão enquanto o editor roda.
    Status 'criando os atalhos'
    $nomeLnk = 'Sharkcut.lnk'
    $naPasta = Join-Path $dir $nomeLnk
    try {
        CriarAtalho $naPasta $iniciar 'Sharkcut — editor de vídeo local'
        Escrever "atalho criado na pasta do programa"
    } catch { Escrever "não consegui criar o atalho na pasta: $($_.Exception.Message)" }

    if ($cxArea.Checked) {
        try {
            CriarAtalho (Join-Path ([Environment]::GetFolderPath('Desktop')) $nomeLnk) `
                        $iniciar 'Sharkcut — editor de vídeo local'
            Escrever 'atalho criado na Área de Trabalho'
        } catch { Escrever "Área de Trabalho: $($_.Exception.Message)" }
    }

    $noMenu = $null
    if ($cxMenu.Checked -or $cxBarra.Checked) {
        try {
            $pastaMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
            $noMenu = Join-Path $pastaMenu $nomeLnk
            CriarAtalho $noMenu $iniciar 'Sharkcut — editor de vídeo local'
            CriarAtalho (Join-Path $pastaMenu 'Sharkcut - liberar espaço.lnk') `
                        (Join-Path $dir 'limpar.bat') 'Apaga cópias antigas e caches'
            Escrever 'atalhos criados no Menu Iniciar'
        } catch { Escrever "Menu Iniciar: $($_.Exception.Message)" }
    }

    $script:avisoBarra = ''
    if ($cxBarra.Checked -and $noMenu) {
        if (FixarNaBarra $noMenu) {
            Escrever 'fixado na barra de tarefas'
        } else {
            $script:avisoBarra = 'Para fixar na barra: abra o Sharkcut, clique com o botão direito no ícone dele na barra e escolha "Fixar na barra de tarefas".'
            Escrever $script:avisoBarra
        }
    }
    Passo 7

    if ($script:avisoBarra) {
        Status ('Pronto. ' + $script:avisoBarra) $corOk
    } else {
        Status 'Pronto. O Sharkcut está instalado.' $corOk
    }
    $script:terminou = $true
    $btInstalar.Text = 'FECHAR'
    $btInstalar.Enabled = $true
    $btInstalar.BackColor = $corPainel
    $btInstalar.ForeColor = $corTexto

    if ($cxAbrir.Checked) {
        try { Start-Process -FilePath $iniciar -WorkingDirectory $dir } catch { }
    }
}

$script:terminou = $false
$btInstalar.Add_Click({
    if ($script:terminou) { $form.Close(); return }
    try {
        Instalar
    } catch {
        Escrever $_.Exception.Message
        Status 'Alguma coisa deu errado — a mensagem está no log.' $corErro
        $script:terminou = $true
        $btInstalar.Text = 'FECHAR'
        $btInstalar.Enabled = $true
    }
})

$form.Add_Shown({
    $form.Activate()
    Conferir | Out-Null
    if (Test-Path $pyVenv) {
        Status 'Ambiente já instalado — isto vai só conferir e atualizar.'
    }
})
[void]$form.ShowDialog()
