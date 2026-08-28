"""Configuração central e descoberta de binários."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "editor-de-video"


def _default_data_dir() -> Path:
    env = os.environ.get("EDITOR_DATA_DIR")
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME


def _default_output_dir() -> Path:
    """Onde o vídeo pronto é salvo.

    NUNCA dentro da pasta de dados do app: no Windows ela fica em
    AppData\\Local, que é oculta por padrão — o usuário exportava e não
    achava o arquivo. O vídeo pronto vai para a pasta de Vídeos, que é onde
    uma pessoa procura um vídeo.
    """
    env = os.environ.get("EDITOR_OUTPUT_DIR")
    if env:
        return Path(env).expanduser()
    casa = Path.home()
    for nome in ("Vídeos", "Videos", "Movies", "Filmes"):
        if (casa / nome).is_dir():
            return casa / nome / "Editor de Vídeo"
    return casa / "Editor de Vídeo"


DATA_DIR = _default_data_dir()
OUTPUT_DIR = _default_output_dir()
PROJECTS_DIR = DATA_DIR / "projects"
CACHE_DIR = DATA_DIR / "cache"
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "editor.sqlite3"
STATIC_DIR = Path(__file__).parent / "static"

HOST = os.environ.get("EDITOR_HOST", "127.0.0.1")
PORT = int(os.environ.get("EDITOR_PORT", "8000"))

# Quantos jobs pesados rodam em paralelo. 1 é o certo: ffmpeg e whisper já
# saturam a máquina sozinhos e concorrência aqui só deixa tudo mais lento.
WORKERS = int(os.environ.get("EDITOR_WORKERS", "1"))

# "auto" = large-v3 quando houver GPU, turbo quando for CPU. Numa CPU o
# large-v3 leva uns 5 minutos por minuto de vídeo, o que na prática faz o
# usuário achar que travou; o turbo tem quase a mesma precisão numa fração do
# tempo. Quem quiser fixar um, é só pôr o nome em EDITOR_WHISPER_MODEL.
WHISPER_MODEL = os.environ.get("EDITOR_WHISPER_MODEL", "auto")
WHISPER_MODEL_GPU = "large-v3"
WHISPER_MODEL_CPU = "turbo"
WHISPER_LANGUAGE = os.environ.get("EDITOR_WHISPER_LANGUAGE", "pt")
WHISPER_DEVICE = os.environ.get("EDITOR_WHISPER_DEVICE", "auto")
WHISPER_COMPUTE = os.environ.get("EDITOR_WHISPER_COMPUTE", "")


def ensure_dirs() -> None:
    for d in (DATA_DIR, PROJECTS_DIR, CACHE_DIR, MEDIA_DIR):
        d.mkdir(parents=True, exist_ok=True)


def output_dir() -> Path:
    """A pasta de saída ATUAL (o usuário pode ter mudado)."""
    from . import db

    try:
        escolhida = db.get_setting("output_dir")
    except Exception:  # noqa: BLE001 — antes do banco existir
        escolhida = None
    alvo = Path(escolhida).expanduser() if escolhida else OUTPUT_DIR
    alvo.mkdir(parents=True, exist_ok=True)
    return alvo


def _find_bin(name: str) -> str:
    """Acha ffmpeg/ffprobe no PATH ou nos lugares óbvios do Windows/macOS."""
    env = os.environ.get(f"EDITOR_{name.upper()}")
    if env and Path(env).exists():
        return env
    found = shutil.which(name)
    if found:
        return found
    exe = name + (".exe" if sys.platform.startswith("win") else "")
    candidates = [
        Path(r"C:/ffmpeg/bin") / exe,
        Path(r"C:/Program Files/ffmpeg/bin") / exe,
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links" / exe,
        Path("/opt/homebrew/bin") / exe,
        Path("/usr/local/bin") / exe,
    ]
    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except OSError:
            continue
    return name  # deixa estourar no subprocess com mensagem clara


FFMPEG = _find_bin("ffmpeg")
FFPROBE = _find_bin("ffprobe")


def ffmpeg_available() -> tuple[bool, str]:
    try:
        out = subprocess.run(
            [FFMPEG, "-version"], capture_output=True, text=True, timeout=20
        )
        if out.returncode == 0:
            return True, out.stdout.splitlines()[0]
        return False, out.stderr.strip()[:400]
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


@dataclass
class CutParams:
    """Parâmetros de corte — todos ajustáveis por preset."""

    silence_min: float = 0.60        # pausa acima disso vira corte
    air: float = 0.20                # ar deixado de cada lado do corte
    margin: float = 0.15             # folga extra em cada trecho preservado
    # Um controle só, de 0 (conservador) a 1 (agressivo), que move os três
    # números juntos. Ninguém deveria precisar decorar três parâmetros para
    # dizer "corta mais em cima".
    aggressiveness: float = -1.0     # < 0 = usa os três valores acima como estão
    adaptive_floor: bool = True      # piso de silêncio medido na fala do usuário
    min_block: float = 1.00          # bloco mínimo
    short_block_policy: str = "keep"  # keep | drop
    narrative_pause: float = 0.80    # fronteira de bloco narrativo
    fade_ms: int = 12                # fade nas emendas de áudio
    snap_neighbor_guard: float = 0.06


@dataclass
class SpeedParams:
    min_speed: float = 0.90
    max_speed: float = 1.40
    warn_above: float = 1.25
    global_multiplier: float = 1.0
    ceiling: float = 1.18            # teto que a proposta automática respeita


@dataclass
class ZoomParams:
    """Zoom automático entre cenas — multicâmera simulada num take único.

    O critério é TEMPO DE TELA ACUMULADO, não troca de frase. Trocar a cada
    bloco fazia o enquadramento piscar duas ou três vezes por segundo, porque
    o corte de silêncio produz blocos de 0,13 s.
    """

    enabled: bool = True
    seconds_per_scene: float = 4.5      # tempo de tela por enquadramento
    # A escada nunca é sorteada. O 1,00 reaparece com frequência porque voltar
    # ao plano aberto dá respiro; uma sequência que só fecha sufoca o vídeo.
    ladder: tuple[float, ...] = (1.00, 1.08, 1.00, 1.14, 1.05,
                                 1.17, 1.00, 1.11, 1.06, 1.14)
    amplitude: float = 0.08             # quanto a escada se afasta de 1,00
    max_zoom: float = 1.15              # teto do preset (a fonte pode baixar)
    intensity: float = 1.0              # multiplicador global, para a mão
    face_x: float = 0.50                # centro do recorte, fração da largura
    face_y: float = 0.44                # fração da altura
    face_method: str = "padrao"         # padrao | movimento | opencv | manual
    # âncora EFETIVA do recorte: o ponto mais perto do rosto que todos os
    # enquadramentos conseguem centrar. Calculada por assign_zoom.
    anchor_x: float = 0.50
    anchor_y: float = 0.50
    unsharp: float = 0.35               # compensa a suavização da reescala


@dataclass
class SubtitleStyle:
    font: str = "Arial"
    fontsize: int = 35
    primary: str = "#FFFFFF"
    outline_color: str = "#000000"
    back_color: str = "#000000"
    bold: bool = True
    italic: bool = False
    outline: float = 4.0
    shadow: float = 1.0
    align: int = 2                   # 2 = inferior centralizado (numpad ASS)
    margin_v: int = 220              # px a partir da base
    margin_l: int = 60
    margin_r: int = 60
    target_width_px: int | None = None  # calibração por largura
    uppercase: bool = False
    max_chars_per_line: int = 24
    max_lines: int = 2
    max_duration: float = 2.6
    extend: float = 0.26
    merge_below_chars: int = 6


@dataclass
class AudioParams:
    highpass: int = 75
    comp_threshold: float = -24.0
    comp_ratio: float = 2.5
    comp_attack: int = 12
    comp_release: int = 220
    comp_makeup: float = 1.0
    comp_knee: float = 6.0
    target_lufs: float = -15.0
    true_peak: float = -1.5
    lra: float = 9.0
    denoise_enabled: bool = False    # DESLIGADO POR PADRÃO. Sempre.
    denoise_chain: str = ""
    presence_gain: float = 0.0
    deesser: float = 0.0


@dataclass
class ExportParams:
    codec: str = "h264"              # h264 | h265
    preset: str = "medium"
    crf: int = 15
    pix_fmt: str = "yuv420p"
    audio_bitrate: str = "256k"
    audio_rate: int = 48000
    burn_subtitles: bool = True
    scale: str = "source"            # nunca reduzir por padrão
    chunk_blocks: int = 12           # exportação em blocos retomável


@dataclass
class Preset:
    name: str
    description: str = ""
    cut: CutParams = field(default_factory=CutParams)
    speed: SpeedParams = field(default_factory=SpeedParams)
    style: SubtitleStyle = field(default_factory=SubtitleStyle)
    audio: AudioParams = field(default_factory=AudioParams)
    export: ExportParams = field(default_factory=ExportParams)
    zoom: ZoomParams = field(default_factory=ZoomParams)
    builtin: bool = False
