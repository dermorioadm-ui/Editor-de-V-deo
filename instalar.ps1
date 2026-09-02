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

function Fonte($tamanho, $estilo = [System.Drawing.FontStyle]::Regular) {
    New-Object System.Drawing.Font('Segoe UI', $tamanho, $estilo)
}

# -------------------------------------------------------------------- janela
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Instalar o Sharkcut'
$form.ClientSize = New-Object System.Drawing.Size(620, 524)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.BackColor = $corFundo
$form.ForeColor = $corTexto
$form.Font = Fonte 9
try { $form.Icon = New-Object System.Drawing.Icon($ico) } catch { }

$topo = New-Object System.Windows.Forms.Panel
$topo.SetBounds(0, 0, 620, 84)
$topo.BackColor = $corPainel
$form.Controls.Add($topo)

$logo = New-Object System.Windows.Forms.PictureBox
$logo.SetBounds(24, 18, 48, 48)
$logo.SizeMode = 'Zoom'
try { $logo.Image = (New-Object System.Drawing.Icon($ico, 48, 48)).ToBitmap() } catch { }
$topo.Controls.Add($logo)

$titulo = New-Object System.Windows.Forms.Label
$titulo.SetBounds(84, 16, 400, 30)
$titulo.Text = 'Sharkcut'
$titulo.Font = Fonte 16 ([System.Drawing.FontStyle]::Bold)
$titulo.ForeColor = $corTexto
$topo.Controls.Add($titulo)

$subtitulo = New-Object System.Windows.Forms.Label
$subtitulo.SetBounds(86, 48, 460, 20)
$subtitulo.Text = 'editor de vídeo local — nada sai da sua máquina'
$subtitulo.ForeColor = $corFraco
$topo.Controls.Add($subtitulo)

function NovoRotulo($x, $y, $largura, $texto, $cor) {
    $l = New-Object System.Windows.Forms.Label
    $l.SetBounds($x, $y, $largura, 20)
    $l.Text = $texto
    $l.ForeColor = $cor
    $form.Controls.Add($l)
    return $l
}

$lblPython = NovoRotulo 26 102 560 '•  Python' $corFraco
$lblDisco  = NovoRotulo 26 126 560 '•  Espaço em disco' $corFraco
$lblFfmpeg = NovoRotulo 26 150 560 '•  ffmpeg' $corFraco

$lblOpcoes = NovoRotulo 26 186 560 'Quando terminar:' $corTexto

function NovaCaixa($y, $texto, $marcada) {
    $c = New-Object System.Windows.Forms.CheckBox
    $c.SetBounds(28, $y, 566, 22)
    $c.Text = $texto
    $c.Checked = $marcada
    $c.ForeColor = $corTexto
    $c.FlatStyle = 'Flat'
    $form.Controls.Add($c)
    return $c
}

$cxArea    = NovaCaixa 210 'Criar o ícone na Área de Trabalho' $true
$cxMenu    = NovaCaixa 234 'Criar o ícone no Menu Iniciar' $true
$cxBarra   = NovaCaixa 258 'Fixar na barra de tarefas (pode pedir um clique seu)' $true
$cxAbrir   = NovaCaixa 282 'Abrir o Sharkcut assim que terminar' $true

$barra = New-Object System.Windows.Forms.ProgressBar
$barra.SetBounds(26, 318, 568, 8)
$barra.Maximum = 7
$barra.Style = 'Continuous'
$form.Controls.Add($barra)

$lblStatus = NovoRotulo 26 332 568 'Pronto para instalar.' $corMarca

$log = New-Object System.Windows.Forms.TextBox
$log.SetBounds(26, 356, 568, 96)
$log.Multiline = $true
$log.ReadOnly = $true
$log.ScrollBars = 'Vertical'
$log.BackColor = [System.Drawing.Color]::FromArgb(11, 15, 20)
$log.ForeColor = $corFraco
$log.Font = New-Object System.Drawing.Font('Consolas', 8)
$log.BorderStyle = 'FixedSingle'
$form.Controls.Add($log)

$btSair = New-Object System.Windows.Forms.Button
$btSair.SetBounds(26, 468, 120, 40)
$btSair.Text = 'Sair'
$btSair.FlatStyle = 'Flat'
$btSair.BackColor = $corPainel
$btSair.ForeColor = $corTexto
$btSair.FlatAppearance.BorderColor = $corLinha
$btSair.Add_Click({ $form.Close() })
$form.Controls.Add($btSair)

$btInstalar = New-Object System.Windows.Forms.Button
$btInstalar.SetBounds(374, 468, 220, 40)
$btInstalar.Text = 'INSTALAR'
$btInstalar.FlatStyle = 'Flat'
$btInstalar.BackColor = $corMarca
$btInstalar.ForeColor = $corFundo
$btInstalar.Font = Fonte 10 ([System.Drawing.FontStyle]::Bold)
$btInstalar.FlatAppearance.BorderSize = 0
$form.Controls.Add($btInstalar)

# ------------------------------------------------------------------ auxílios
function Escrever($texto) {
    if (-not $texto) { return }
    $log.AppendText(($texto.TrimEnd() + "`r`n"))
    [System.Windows.Forms.Application]::DoEvents()
}

function Status($texto, $cor = $null) {
    $lblStatus.Text = $texto
    if ($cor) { $lblStatus.ForeColor = $cor } else { $lblStatus.ForeColor = $corMarca }
    [System.Windows.Forms.Application]::DoEvents()
}

function Passo($n) {
    $barra.Value = [Math]::Min($n, $barra.Maximum)
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
    return $p.ExitCode
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
        $lblDisco.Text = "✓  $livre GB livres"
        $lblDisco.ForeColor = $corOk
    } else {
        $lblDisco.Text = "✗  $livre GB livres, precisa de ~$precisa GB — rode o limpar.bat"
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
        if ($r -ne 0 -or -not (Test-Path $pyVenv)) {
            Status 'Não consegui criar o ambiente. O motivo está no log acima.' $corErro
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
        Status 'A instalação das bibliotecas falhou — o motivo está no log.' $corErro
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
