"""Wrappers de ffmpeg/ffprobe.

Regra da casa: nada aqui reencoda por conveniência. Quem chama decide.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

from .config import FFMPEG, FFPROBE

_CREATE_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


class FFmpegError(RuntimeError):
    def __init__(self, cmd: Sequence[str], returncode: int, stderr: str):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-14:])
        super().__init__(f"ffmpeg falhou (código {returncode}):\n{tail}")


# Prioridade BAIXA para o trabalho de fundo. A exportação e a prévia rodam
# enquanto o usuário retoca; em prioridade normal elas disputam os 4 núcleos
# de igual para igual com o navegador e a interface engasga. Em prioridade
# baixa o sistema dá a CPU para quem está interagindo e o ffmpeg fica com o
# que sobra — que, com o usuário só olhando, é tudo.
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
_FUNDO = {"ativo": False}


def em_segundo_plano(ativo: bool = True) -> None:
    """Liga/desliga a prioridade baixa para os próximos processos ffmpeg."""
    _FUNDO["ativo"] = bool(ativo)


def _popen(cmd: Sequence[str], **kw):
    flags = _CREATE_NO_WINDOW
    pre = None
    if _FUNDO["ativo"]:
        if sys.platform.startswith("win"):
            flags |= _BELOW_NORMAL
        else:
            def pre() -> None:  # noqa: E306 — nice(10) só no filho
                try:
                    import os as _os
                    _os.nice(10)
                except Exception:  # noqa: BLE001
                    pass
    return subprocess.Popen(
        list(cmd),
        stdout=kw.pop("stdout", subprocess.PIPE),
        stderr=kw.pop("stderr", subprocess.PIPE),
        creationflags=flags,
        preexec_fn=pre,
        **kw,
    )


def run(cmd: Sequence[str], timeout: float | None = None) -> str:
    proc = _popen(cmd)
    out, err = proc.communicate(timeout=timeout)
    stderr = (err or b"").decode("utf-8", "replace")
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, stderr)
    return (out or b"").decode("utf-8", "replace")


_PROGRESS_RE = re.compile(rb"out_time_us=(-?\d+)")
_FRAME_RE = re.compile(rb"frame=(\d+)")


def run_with_progress(
    cmd: Sequence[str],
    total_seconds: float | None,
    on_progress: Callable[[float], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> "RunStats":
    """Roda ffmpeg com -progress e reporta fração 0..1."""
    full = [cmd[0], "-nostdin", "-progress", "pipe:1", "-nostats", *cmd[1:]]
    proc = _popen(full)
    stats = RunStats()
    err_chunks: list[bytes] = []
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            m = _PROGRESS_RE.search(raw)
            if m:
                us = int(m.group(1))
                if us >= 0:
                    stats.out_time = us / 1e6
                    if on_progress and total_seconds:
                        on_progress(min(1.0, stats.out_time / max(total_seconds, 1e-6)))
            m = _FRAME_RE.search(raw)
            if m:
                stats.frames = int(m.group(1))
            if cancel and cancel():
                proc.kill()
                raise KeyboardInterrupt("cancelado")
    finally:
        if proc.stderr is not None:
            err_chunks.append(proc.stderr.read() or b"")
        proc.wait()
    stderr = b"".join(err_chunks).decode("utf-8", "replace")
    if proc.returncode != 0:
        raise FFmpegError(full, proc.returncode, stderr)
    stats.stderr = stderr
    return stats


@dataclass
class RunStats:
    out_time: float = 0.0
    frames: int = 0
    stderr: str = ""


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 30.0
    v_codec: str = ""
    a_codec: str = ""
    bitrate: int = 0
    v_bitrate: int = 0
    sample_rate: int = 48000
    channels: int = 2
    pix_fmt: str = ""
    color_primaries: str = ""
    color_transfer: str = ""
    color_space: str = ""
    rotation: int = 0
    size_bytes: int = 0
    has_audio: bool = True
    has_video: bool = True
    raw: dict = field(default_factory=dict)

    @property
    def is_hdr(self) -> bool:
        t = (self.color_transfer or "").lower()
        p = (self.color_primaries or "").lower()
        return ("arib-std-b67" in t or "smpte2084" in t or "pq" in t
                or "bt2020" in p)

    @property
    def display_size(self) -> tuple[int, int]:
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "raw"}
        d["is_hdr"] = self.is_hdr
        d["display_width"], d["display_height"] = self.display_size
        return d


def _parse_fraction(value: str | None, default: float = 0.0) -> float:
    if not value:
        return default
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else default
        return float(value)
    except (TypeError, ValueError):
        return default


def probe(path: str | Path) -> MediaInfo:
    out = run([
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    data = json.loads(out)
    info = MediaInfo(path=str(path), raw=data)
    fmt = data.get("format", {})
    info.duration = float(fmt.get("duration") or 0.0)
    info.bitrate = int(float(fmt.get("bit_rate") or 0))
    info.size_bytes = int(float(fmt.get("size") or 0))
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    info.has_video = v is not None
    info.has_audio = a is not None
    if v:
        info.width = int(v.get("width") or 0)
        info.height = int(v.get("height") or 0)
        info.fps = _parse_fraction(v.get("avg_frame_rate")) or _parse_fraction(
            v.get("r_frame_rate"), 30.0
        )
        if info.fps <= 0:
            info.fps = 30.0
        info.v_codec = v.get("codec_name", "")
        info.pix_fmt = v.get("pix_fmt", "")
        info.color_primaries = v.get("color_primaries", "")
        info.color_transfer = v.get("color_transfer", "")
        info.color_space = v.get("color_space", "")
        info.v_bitrate = int(float(v.get("bit_rate") or 0))
        if not info.duration:
            info.duration = float(v.get("duration") or 0.0)
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                info.rotation = int(abs(float(sd["rotation"]))) % 360
        tags = v.get("tags") or {}
        if "rotate" in tags:
            try:
                info.rotation = int(abs(float(tags["rotate"]))) % 360
            except ValueError:
                pass
    if a:
        info.a_codec = a.get("codec_name", "")
        info.sample_rate = int(a.get("sample_rate") or 48000)
        info.channels = int(a.get("channels") or 2)
    if not info.v_bitrate and info.bitrate and info.has_video:
        # estimativa: tira ~256 kbps do áudio
        info.v_bitrate = max(0, info.bitrate - (256_000 if info.has_audio else 0))
    return info


def extract_wav(
    src: str | Path,
    dest: str | Path,
    sample_rate: int = 16000,
    channels: int = 1,
    on_progress: Callable[[float], None] | None = None,
    duration: float | None = None,
) -> Path:
    """WAV mono 16 kHz — é o que o whisper e a análise de sinal consomem."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y", "-i", str(src), "-vn",
        "-ac", str(channels), "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", "-f", "wav", str(dest),
    ]
    run_with_progress(cmd, duration, on_progress)
    return dest


def read_wav_mono(path: str | Path) -> tuple[np.ndarray, int]:
    """Lê WAV PCM 16-bit sem depender de scipy."""
    import wave

    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        ch = wf.getnchannels()
        width = wf.getsampwidth()
        raw = wf.readframes(n)
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise ValueError(f"WAV com {width*8} bits não suportado")
    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    scale = float(np.iinfo(dtype).max)
    data /= scale
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


def decode_pcm(
    src: str | Path,
    start: float | None = None,
    end: float | None = None,
    sample_rate: int = 48000,
    channels: int = 1,
    filters: str | None = None,
) -> np.ndarray:
    """Decodifica um trecho para float32 na memória (sem arquivo intermediário)."""
    cmd = [FFMPEG, "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, start):.6f}"]
    if end is not None:
        cmd += ["-to", f"{end:.6f}"]
    cmd += ["-i", str(src), "-vn"]
    if filters:
        cmd += ["-af", filters]
    cmd += ["-ac", str(channels), "-ar", str(sample_rate),
            "-f", "f32le", "-c:a", "pcm_f32le", "pipe:1"]
    proc = _popen(cmd)
    out, err = proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, (err or b"").decode("utf-8", "replace"))
    return np.frombuffer(out, dtype=np.float32).copy()


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int = 48000) -> Path:
    import wave

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return path


def measure_render(
    cmd_without_output: Sequence[str],
    total_seconds: float | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> tuple[float, int]:
    """Mede a duração REAL de um filtro-grafo sem encodar nada.

    Roda o mesmo grafo para ``-f null``. O ffmpeg decodifica, aplica setpts e
    reporta ``out_time``/``frame`` finais — que é exatamente o que o encode
    produziria. Serve para montar a linha do tempo das legendas sem gastar uma
    geração de encode (Parte 5.1 e Parte 10.1).
    """
    cmd = [*cmd_without_output, "-f", "null", "-"]
    stats = run_with_progress(cmd, total_seconds, on_progress)
    return stats.out_time, stats.frames


def concat_demux(segments: Iterable[str | Path], dest: str | Path,
                 extra: Sequence[str] = ()) -> Path:
    """Concatena segmentos já encodados SEM reencodar (-c copy)."""
    dest = Path(dest)
    seg_list = [Path(s) for s in segments]
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as fh:
        for s in seg_list:
            escaped = str(s.resolve()).replace("\\", "/").replace("'", r"'\''")
            fh.write(f"file '{escaped}'\n")
        list_path = fh.name
    try:
        run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", list_path, "-c", "copy", *extra, str(dest)])
    finally:
        Path(list_path).unlink(missing_ok=True)
    return dest


def escape_filter_path(path: str | Path) -> str:
    """Escapa caminho para dentro de um filtergraph (Windows precisa disso)."""
    s = str(Path(path).resolve()).replace("\\", "/")
    s = s.replace(":", r"\:").replace("'", r"\'").replace("[", r"\[").replace("]", r"\]")
    return s


def hw_encoders() -> list[str]:
    """O que este ffmpeg foi COMPILADO com — não o que a máquina consegue usar."""
    try:
        out = run([FFMPEG, "-v", "error", "-hide_banner", "-encoders"])
    except Exception:  # noqa: BLE001
        return []
    names = []
    for line in out.splitlines():
        for enc in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv",
                    "h264_videotoolbox", "hevc_videotoolbox", "h264_amf"):
            if f" {enc} " in line and enc not in names:
                names.append(enc)
    return names


_HW_TESTADO: list[str] | None = None


def hw_encoder_utilizavel() -> str | None:
    """O encoder de GPU que REALMENTE encoda nesta máquina — ou None.

    A lista de `-encoders` é a lista da compilação: o ffmpeg de qualquer
    Windows anuncia h264_nvenc mesmo numa máquina sem placa NVIDIA, e aí o
    encode morre no primeiro trecho. Como o clique único exporta sozinho, um
    encoder que não existe não deixaria o usuário lento: deixaria sem vídeo.
    Então aqui a gente ENCODA um quadro de teste e só devolve o que passou.
    """
    global _HW_TESTADO
    if _HW_TESTADO is not None:
        return _HW_TESTADO[0] if _HW_TESTADO else None
    bons: list[str] = []
    for enc in hw_encoders():
        try:
            subprocess.run(
                [FFMPEG, "-v", "error", "-f", "lavfi",
                 "-i", "color=c=black:s=256x256:d=0.1:r=10",
                 "-c:v", enc, "-frames:v", "3", "-f", "null", "-"],
                check=True, capture_output=True, timeout=25,
            )
            bons.append(enc)
        except Exception:  # noqa: BLE001 — anunciado mas inutilizável
            continue
    _HW_TESTADO = bons
    return bons[0] if bons else None
