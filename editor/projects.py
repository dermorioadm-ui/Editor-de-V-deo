"""Serviço de projeto: análise, plano, legendas, exportação e validação."""
from __future__ import annotations

import copy
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import db, presets as presets_mod
from .audio.align import long_silences_inside, trim_words
from .audio.segments import split_narrative
from .audio.clap import build_discarded_takes, detect_claps
from .audio.whistle import detect_whistles
from .audio.envelope import Envelope, compute_envelope
from .config import (PROJECTS_DIR, AudioParams, CutParams, ExportParams,
                     SpeedParams, SubtitleStyle, ZoomParams, ensure_dirs,
                     output_dir)
from .edit.audit import audit_edges, audit_summary, settle_edges
from .edit.plan_builder import (build_auto_plan, resync_removed,
                                words_removed_by_takes)
from .edit.repeats import find_repeats
from .edit.zoom import assign_zoom, auditar as zoom_auditar, cenas as zoom_cenas
from .video_analysis import face_center
from .edit.repeats import removed_word_ids as repeats_removed_ids
from .edit.timeline import Timeline
from .ffmpeg_utils import (MediaInfo, extract_wav, hw_encoder_utilizavel, probe,
                           read_wav_mono)
from .models import EditPlan, Subtitle
from .render.export import export_project
from .render.validate import validate_export
from .subtitles.corrections import apply_corrections
from .subtitles.fillers import annotate as annotate_fillers
from .subtitles.linebreak import build_cues, wrap
from .subtitles.remap import remap_words

_envelope_cache: dict[str, Envelope] = {}


# ------------------------------------------------------------------ projeto
class Project:
    def __init__(self, row) -> None:
        self.id = row["id"]
        self.name = row["name"]
        self.source_path = row["source_path"]
        self.preset = row["preset"]
        self.status = row["status"]
        self.info = MediaInfo(**{k: v for k, v in db.jloads(row["info_json"]).items()
                                 if k in MediaInfo.__dataclass_fields__}) \
            if row["info_json"] else None
        self.analysis = db.jloads(row["analysis_json"], {})
        self.plan = EditPlan.from_dict(db.jloads(row["plan_json"], {}))
        self.plan.project_id = self.id

    @property
    def dir(self) -> Path:
        d = PROJECTS_DIR / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def wav(self) -> Path:
        return self.dir / "audio16k.wav"

    @property
    def envelope_file(self) -> Path:
        return self.dir / "envelope.npy"

    @property
    def proxy_file(self) -> Path:
        """Cópia leve da FONTE, só para a prévia tocar liso."""
        return self.dir / "proxy.mp4"

    @property
    def proxy_ok(self) -> bool:
        """O proxy existe e é do arquivo que está aberto agora?

        Se o usuário trocar o arquivo fonte, o proxy velho tem que morrer —
        senão ele edita vendo um vídeo e exporta outro.
        """
        f = self.proxy_file
        if not f.exists() or f.stat().st_size < 1024:
            return False
        marca = self.dir / "proxy.origem"
        if not marca.exists():
            return False
        try:
            src = Path(self.source_path)
            atual = f"{src.resolve()}|{src.stat().st_size}|{int(src.stat().st_mtime)}"
        except OSError:
            return False
        return marca.read_text(encoding="utf-8").strip() == atual

    def marcar_proxy(self) -> None:
        src = Path(self.source_path)
        (self.dir / "proxy.origem").write_text(
            f"{src.resolve()}|{src.stat().st_size}|{int(src.stat().st_mtime)}",
            encoding="utf-8")

    @property
    def words(self) -> list[dict]:
        return self.analysis.get("words", [])

    def envelope(self) -> Envelope | None:
        cached = _envelope_cache.get(self.id)
        if cached is not None:
            return cached
        if not self.envelope_file.exists():
            return None
        db_array = np.load(self.envelope_file)
        meta = self.analysis.get("envelope", {})
        env = Envelope(db_array, meta.get("hop", 0.010),
                       meta.get("sample_rate", 16000))
        _envelope_cache[self.id] = env
        return env

    def save_plan(self) -> None:
        db.ex("UPDATE projects SET plan_json=?, updated_at=? WHERE id=?",
              (db.jdumps(self.plan.to_dict()), time.time(), self.id))

    def save_analysis(self) -> None:
        db.ex("UPDATE projects SET analysis_json=?, updated_at=? WHERE id=?",
              (db.jdumps(self.analysis), time.time(), self.id))

    def set_status(self, status: str) -> None:
        self.status = status
        db.ex("UPDATE projects SET status=?, updated_at=? WHERE id=?",
              (status, time.time(), self.id))

    def to_dict(self, full: bool = False) -> dict:
        out = {
            "id": self.id, "name": self.name, "source_path": self.source_path,
            "preset": self.preset, "status": self.status,
            "info": self.info.to_dict() if self.info else None,
            "media": list_media(self.id),
        }
        if full:
            out["analysis"] = self.analysis
            out["plan"] = self.plan.to_dict()
            out["timeline"] = timeline_summary(self)
        return out


def create(source_path: str, name: str = "", preset: str = "VSL") -> Project:
    ensure_dirs()
    src = Path(source_path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"arquivo não encontrado: {src}")
    info = probe(src)
    pid = uuid.uuid4().hex[:12]
    plan = EditPlan(project_id=pid, preset=preset)
    apply_preset_to_plan(plan, preset)
    escalar_legenda(plan, info)
    now = time.time()
    db.ex(
        "INSERT INTO projects(id, name, source_path, preset, status, info_json, "
        "plan_json, analysis_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pid, name or src.stem, str(src.resolve()), preset, "novo",
         db.jdumps(info.to_dict()), db.jdumps(plan.to_dict()), "{}", now, now),
    )
    return load(pid)


def load(pid: str) -> Project:
    row = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not row:
        raise KeyError(f"projeto {pid} não encontrado")
    return Project(row)


def list_projects() -> list[dict]:
    rows = db.q("SELECT * FROM projects ORDER BY updated_at DESC")
    out = []
    for r in rows:
        info = db.jloads(r["info_json"], {})
        out.append({
            "id": r["id"], "name": r["name"], "source_path": r["source_path"],
            "preset": r["preset"], "status": r["status"],
            "duration": info.get("duration", 0), "width": info.get("width"),
            "height": info.get("height"), "updated_at": r["updated_at"],
        })
    return out


def delete_project(pid: str) -> None:
    db.ex("DELETE FROM projects WHERE id=?", (pid,))
    db.ex("DELETE FROM media WHERE project_id=?", (pid,))
    _envelope_cache.pop(pid, None)
    shutil.rmtree(PROJECTS_DIR / pid, ignore_errors=True)


# O PADRÃO DA LEGENDA DEPENDE DA PROPORÇÃO DO QUADRO, não só da altura.
#
# Escalar tudo pela altura era o que havia, e num vídeo VERTICAL funciona: os
# 35 px do preset, medidos numa altura de referência de 1024, viram 66 num
# 1080x1920 — 3,4% da altura, que é o tamanho que o usuário aprovou olhando.
#
# Num vídeo HORIZONTAL a mesma conta erra feio. A margem de 220 px do preset
# é 21,5% da altura, e ela existe para escapar da barra do Instagram no
# vertical; num 16:9 ela joga a legenda para o meio do peito de quem fala.
# E 3,4% da altura num quadro largo fica pequeno demais para a distância de
# leitura de uma tela de computador.
#
# Então cada proporção tem o seu padrão, em FRAÇÃO DO QUADRO:
#
#   (proporção mínima, fonte, margem de baixo, contorno, chars por linha)
#
# vertical    3,4% / 21,5% — o que ele aprovou, e a margem que escapa da UI
# quadrado    4,0% / 11,0% — feed, sem barra por cima
# horizontal  4,6% /  8,0% — a régua de legendagem de cinema e streaming
PADROES_DE_LEGENDA = (
    (1.50, 0.046, 0.080, 0.0044, 42),
    (0.90, 0.040, 0.110, 0.0042, 32),
    (0.00, 0.034, 0.215, 0.0039, 24),
)


def padrao_de_legenda(largura: int, altura: int) -> tuple[float, float, float, int]:
    """(fonte, margem_v, contorno, chars) em pixels para ESTE quadro."""
    prop = largura / max(altura, 1e-9)
    for minima, f, m, c, ch in PADROES_DE_LEGENDA:
        if prop >= minima:
            return altura * f, altura * m, altura * c, ch
    f, m, c, ch = PADROES_DE_LEGENDA[-1][1:]
    return altura * f, altura * m, altura * c, ch


def escalar_legenda(plan: EditPlan, info, fator: float = 1.0) -> None:
    """Põe a legenda no padrão do formato do vídeo.

    ``fator`` é o empurrãozinho do usuário na primeira tela (menor/maior). Ele
    multiplica só o TAMANHO — a posição continua sendo a do padrão, senão
    escolher "maior" mandaria a legenda para cima do rosto.
    """
    try:
        largura, altura = (int(x) for x in info.display_size)
    except Exception:  # noqa: BLE001
        return
    if altura < 200 or largura < 200:
        return
    fonte, margem, contorno, chars = padrao_de_legenda(largura, altura)
    st = plan.style
    st.fontsize = max(8, int(round(fonte * max(0.4, float(fator)))))
    st.margin_v = max(0, int(round(margem)))
    st.outline = round(max(1.0, contorno), 2)
    st.shadow = round(max(0.0, contorno * 0.25), 2)
    # margens laterais: a legenda nunca encosta na borda — 6% de cada lado
    st.margin_l = st.margin_r = max(0, int(round(largura * 0.06)))
    st.max_chars_per_line = chars


def fontsize_do_formato(plan: EditPlan, info, fator: float = 1.0) -> int:
    """O tamanho de legenda do padrão deste formato, vezes o empurrão do usuário.

    Sempre recalculado do PADRÃO, nunca do valor que já está no plano: assim
    gerar duas vezes com "maior" não vira "gigante".
    """
    try:
        largura, altura = (int(x) for x in info.display_size)
    except Exception:  # noqa: BLE001
        return plan.style.fontsize
    if altura < 200 or largura < 200:
        return plan.style.fontsize
    fonte, _m, _c, _ch = padrao_de_legenda(largura, altura)
    return max(8, int(round(fonte * max(0.4, float(fator)))))


def apply_preset_to_plan(plan: EditPlan, preset_name: str) -> None:
    data = presets_mod.get_preset(preset_name)
    if not data:
        return
    plan.preset = preset_name
    plan.cut = CutParams(**data.get("cut", {}))
    plan.speed = SpeedParams(**data.get("speed", {}))
    plan.style = SubtitleStyle(**data.get("style", {}))
    plan.audio = AudioParams(**data.get("audio", {}))
    plan.export = ExportParams(**data.get("export", {}))
    zdata = dict(data.get("zoom") or {})
    if "levels" in zdata:
        zdata["levels"] = tuple(float(x) for x in zdata["levels"])
    plan.zoom = ZoomParams(**zdata) if zdata else ZoomParams()


# -------------------------------------------------------------------- mídia
def list_media(pid: str) -> list[dict]:
    return [{"id": r["id"], "path": r["path"], "kind": r["kind"],
             "name": r["name"], "info": db.jloads(r["info_json"], {}),
             "descricao": (r["descricao"] if "descricao" in r.keys() else "") or ""}
            for r in db.q("SELECT * FROM media WHERE project_id=? ORDER BY created_at",
                          (pid,))]


def add_media(pid: str, path: str, kind: str = "video",
              name: str = "", descricao: str = "") -> dict:
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f"arquivo não encontrado: {src}")
    info = {}
    if kind in ("video", "audio"):
        info = probe(src).to_dict()
    elif kind == "image":
        try:
            info = probe(src).to_dict()
        except Exception:  # noqa: BLE001
            info = {}
    mid = uuid.uuid4().hex[:10]
    db.ex("INSERT INTO media(id, project_id, path, kind, name, info_json, "
          "descricao, created_at) VALUES (?,?,?,?,?,?,?,?)",
          (mid, pid, str(src.resolve()), kind, name or src.name,
           db.jdumps(info), str(descricao or "")[:400], time.time()))
    return {"id": mid, "path": str(src.resolve()), "kind": kind,
            "name": name or src.name, "info": info,
            "descricao": str(descricao or "")[:400]}


def sources_for(project: Project) -> dict:
    sources = {"main": {"path": project.source_path, "info": project.info}}
    for m in list_media(project.id):
        info = None
        if m["info"]:
            info = MediaInfo(**{k: v for k, v in m["info"].items()
                                if k in MediaInfo.__dataclass_fields__})
        sources[m["id"]] = {"path": m["path"], "info": info, "kind": m["kind"]}
    return sources


# ------------------------------------------------------------------ análise
def analyze(project: Project, ctx) -> dict:
    """Fase 1: áudio -> envelope -> transcrição -> palmas -> takes."""
    from .transcribe import detect_device, transcribe

    info = project.info or probe(project.source_path)
    ctx.stage("audio", "extraindo áudio (WAV mono 16 kHz)")
    extract_wav(project.source_path, project.wav, 16000, 1,
                on_progress=lambda f: ctx.progress(0.02 + f * 0.10,
                                                   "extraindo áudio"),
                duration=info.duration)

    ctx.stage("envelope", "calculando o envelope de energia")
    samples, sr = read_wav_mono(project.wav)
    env = compute_envelope(samples, sr)
    np.save(project.envelope_file, env.db)
    _envelope_cache[project.id] = env
    ctx.progress(0.16, f"piso de ruído em {env.noise_floor:.1f} dB")

    ctx.stage("palmas", "procurando palmas")
    claps = detect_claps(samples, sr, env)
    ctx.progress(0.20, f"{len(claps)} candidato(s) a palma")

    ctx.stage("transcricao", "transcrevendo")
    device = detect_device()
    result = transcribe(
        samples, info.duration, silence=env.all_silence_runs(0.5),
        on_progress=lambda f, m: ctx.progress(0.22 + f * 0.68, m),
        device_info=device,
    )
    # O Whisper devolve fronteira de ALINHAMENTO, não fronteira acústica:
    # uma palavra de duas letras vem ocupando cinco segundos, e esses cinco
    # segundos são uma pausa escondida dentro de uma palavra. Sem encaixar
    # isto no áudio, o corte de silêncio simplesmente não acontece — o buraco
    # entre palavras, de onde o corte nasce, não existe.
    ctx.stage("encaixe", "encaixando as palavras no áudio")
    words, encaixes = trim_words(result["words"], env)
    if encaixes:
        ctx.progress(0.9, f"{len(encaixes)} palavra(s) estavam esticadas por cima "
                          f"de pausa; encaixadas no som")

    # Onde está o rosto: medido UMA vez, por mediana de várias amostras de
    # movimento. Todo recorte de zoom é concêntrico neste ponto — sem isso o
    # rosto muda de lugar na tela a cada corte e o olho cansa.
    if project.plan.zoom.face_method in ("", "padrao"):
        ctx.stage("rosto", "procurando o centro do rosto")
        try:
            centro = face_center(project.source_path, info.duration)
            project.plan.zoom.face_x = centro["x"]
            project.plan.zoom.face_y = centro["y"]
            project.plan.zoom.face_method = centro["method"]
            project.save_plan()
            ctx.progress(0.93, f"rosto em {centro['x']:.2f}, {centro['y']:.2f} "
                               f"({centro['detail']})")
        except Exception as exc:  # noqa: BLE001
            ctx.progress(0.93, f"não deu para achar o rosto: {exc}")

    # COMANDOS FALADOS — "corta" apaga a tentativa, "próximo" aprova. Ideia do
    # usuário, e a mais robusta das três formas de marcar: o Whisper já
    # transcreveu a palavra com o tempo exato, então não existe falso positivo
    # de acústica. Cada comando vira um marcador SINTÉTICO do tipo certo e
    # reaproveita toda a maquinaria: barreira, take, corte rente, bandeirinha.
    ctx.stage("comandos", "procurando comandos falados")
    from .audio.comandos import detectar as detectar_comandos, ids_de_comando
    comandos = detectar_comandos(words)
    previous = project.analysis or {}
    for cmd in comandos:
        for antigo in previous.get("comandos", []):
            if (abs(float(antigo.get("time", -1)) - cmd.time) < 0.15
                    and antigo.get("enabled") is False):
                cmd.enabled = False
    if comandos:
        n_corta = sum(1 for c in comandos if c.tipo == "corta" and c.enabled)
        n_ok = len([c for c in comandos if c.enabled]) - n_corta
        ctx.progress(0.935, f'{n_corta}x "corta" e {n_ok}x "próximo" ditos — '
                            f"as palavras de comando saem do vídeo")

    ctx.stage("assobio", "procurando assobios")
    freq = project.plan.whistle_freq or None
    assobios = detect_whistles(samples, sr, env, freq_alvo=freq)
    # a decisão do usuário sobrevive à reanálise: assobio desligado continua
    for a in assobios:
        for antigo in previous.get("whistles", []):
            if (abs(float(antigo.get("time", -1)) - a.time) < 0.15
                    and antigo.get("enabled") is False):
                a.enabled = False
    if assobios:
        ctx.progress(0.94, f"{len(assobios)} assobio(s) — take validado, corte rente")

    # Se palma e assobio caírem no mesmo lugar, quem vence é o assobio.
    # A porta de concentração em clap.py já separa os dois, mas errar para o
    # lado de APAGAR FALA é caro e errar para o lado de perder um corte rente
    # é barato — a rede fica aqui, explícita.
    if assobios:
        antes = len(claps)
        claps = [c for c in claps
                 if not any(a.start - 0.10 <= c.time <= a.end + 0.10
                            for a in assobios if a.enabled)]
        if len(claps) < antes:
            ctx.progress(0.945, f"{antes - len(claps)} palma(s) eram assobio; "
                                f"a fala fica")

    # os comandos entram como eventos sintéticos DEPOIS da rede acústica,
    # para nenhum filtro de som derrubar uma palavra dita com todas as letras
    from .audio.clap import ClapEvent
    from .audio.whistle import WhistleEvent
    for cmd in comandos:
        if cmd.tipo == "corta":
            claps.append(ClapEvent(
                id=cmd.id, time=cmd.time, start=cmd.start, end=cmd.end,
                peak_db=0.0, jump_db=0.0, duration=round(cmd.end - cmd.start, 3),
                confirmed=True, suspect=False, attack_floor_db=0.0,
                timbre_score=3, enabled=cmd.enabled,
                reason=f'você disse "{cmd.texto}"'))
        else:
            assobios.append(WhistleEvent(
                id=cmd.id, time=cmd.time, start=cmd.start, end=cmd.end,
                duration=round(cmd.end - cmd.start, 3), freq=0.0, grave=0.0,
                concentracao=1.0, deriva=0.0, peak_db=0.0, enabled=cmd.enabled,
                reason=f'você disse "{cmd.texto}"'))
    claps.sort(key=lambda c: c.time)
    assobios.sort(key=lambda a: a.time)

    ctx.stage("takes", "aplicando a regra do take")

    fillers = annotate_fillers(words, env)
    # decisões do usuário sobrevivem a uma reanálise. Agora só existe UMA
    # decisão possível: "isto não era palma" — casada pelo instante do pico.
    for clap in claps:
        for old in previous.get("claps", []):
            if (abs(float(old.get("time", -1)) - clap.time) < 0.05
                    and old.get("enabled") is False):
                clap.enabled = False
    takes = [t.to_dict() for t in build_discarded_takes(env, claps, words,
                                                        whistles=assobios)]

    for take in takes:
        for old in previous.get("takes", []):
            if abs(float(old.get("start", -9)) - take["start"]) < 0.2                     and old.get("restored"):
                take["restored"] = True
    project.analysis = {
        "duration": info.duration,
        "words": words,
        "segments": result.get("segments", []),
        "device": result.get("device", {}),
        "model": result.get("model"),
        "language": result.get("language"),
        "claps": [c.to_dict() for c in claps],
        "whistles": [a.to_dict() for a in assobios],
        "takes": takes,
        "fillers": fillers,
        "word_fixes": encaixes,
        "envelope": {"hop": env.hop, "sample_rate": env.sample_rate,
                     "noise_floor": env.noise_floor,
                     "silence_threshold": env.silence_threshold,
                     "speech_threshold": env.speech_threshold,
                     "audit_threshold": env.audit_threshold,
                     "duration": env.duration},
        "manual_removed_word_ids": previous.get("manual_removed_word_ids", []),
        "comandos": [c.to_dict() for c in comandos],
        "command_word_ids": sorted(ids_de_comando(comandos)),
        "analyzed_at": time.time(),
    }
    project.save_analysis()
    project.set_status("analisado")
    ctx.progress(1.0, f"{len(words)} palavras, {len(claps)} palma(s), "
                      f"{len(takes)} take(s) descartado(s)")
    return {
        "words": len(words), "claps": len(claps), "takes": len(takes),
        "fillers": len(fillers), "device": result.get("device", {}),
        "noise_floor": round(env.noise_floor, 2),
    }


def _cortes_da_ia(project: Project, ctx, words: list[dict],
                  claps: list[dict], whistles: list[dict]) -> dict | None:
    """Chama a IA para decidir os cortes. None = modo desligado ou sem chave.

    NUNCA levanta exceção: a análise não pode morrer porque a internet caiu.
    Falha vira {"ok": False, "erro": ...} e a regra determinística assume.
    """
    from . import db
    from .ai import cortes as cortes_ia, gemini

    # A etapa SEMPRE aparece, mesmo quando não roda. Pular em silêncio foi o
    # defeito relatado: o usuário via a etapa da IA sumir da lista e não tinha
    # como saber que era falta de chave — ainda mais porque a chave só existia
    # dentro do editor, que só abre DEPOIS deste processamento.
    ctx.stage("ia", "a IA vai decidir os cortes")
    if not db.get_setting("ai_cortes", True):
        ctx.progress(0.95, "IA desligada nos ajustes: corte pela regra do programa")
        return {"ok": False, "erro": "desligada", "pulada": True}
    chave = gemini.chave_guardada()
    if not chave:
        ctx.progress(0.95, "sem chave do Gemini: corte pela regra do programa. "
                           "Cole a chave na tela inicial e aperte refazer edição")
        return {"ok": False, "erro": "sem chave", "pulada": True}
    ctx.progress(0.955, "o vídeo NÃO sai da máquina — vai só o texto")
    try:
        saida = cortes_ia.decidir(chave, db.get_setting("gemini_model", "") or "",
                                  words, claps, whistles,
                                  env=project.envelope(),
                                  vicios=project.analysis.get("fillers") or [])
    except gemini.ErroDaIA as exc:
        ctx.progress(0.96, f"IA indisponível ({exc}); a regra do programa "
                           f"decide sozinha desta vez")
        return {"ok": False, "erro": str(exc)}
    except Exception as exc:  # noqa: BLE001 — a análise sobrevive a tudo
        ctx.progress(0.96, f"IA falhou ({exc}); a regra do programa decide")
        return {"ok": False, "erro": str(exc)}
    if saida.get("modelo_trocado_de"):
        ctx.progress(0.965,
                     f"ATENÇÃO: o modelo fixado ({saida['modelo_trocado_de']}) "
                     f"não está mais disponível nesta chave; usei "
                     f"{saida['modelo']}. Escolha outro na tela inicial.")
    if saida.get("ok"):
        ctx.progress(0.97, f"{saida['modelo']}: {len(saida['takes'])} trecho(s) "
                           f"para fora — cada um na lista, com motivo e volta")
    else:
        motivo = (saida.get("recusados") or [{}])[-1].get("motivo", "resposta ruim")
        ctx.progress(0.97, f"resposta da IA recusada: {motivo}")
    return saida


def auto_edit(project: Project, ctx) -> dict:
    """Fase 2: proposta de cortes, velocidades e legendas."""
    env = project.envelope()
    if env is None:
        raise RuntimeError("rode a análise antes")
    # Encaixa de novo, por segurança. É idempotente — o encaixe só encolhe,
    # e encolher uma palavra já encolhida não muda nada. Serve para o projeto
    # analisado por uma versão antiga: "refazer edição" conserta o corte de
    # silêncio sem precisar transcrever tudo de novo.
    words, encaixes_agora = trim_words(project.words, env)
    if encaixes_agora:
        project.analysis["words"] = words
        antes = project.analysis.get("word_fixes", [])
        vistos = {f["i"] for f in antes}
        project.analysis["word_fixes"] = antes + [f for f in encaixes_agora
                                                  if f["i"] not in vistos]
    takes = list(project.analysis.get("takes", []))

    # A IA DECIDE OS CORTES. Roda AQUI, não na análise: assim colar a chave e
    # apertar "refazer edição" já vale, sem transcrever o vídeo inteiro de
    # novo — que era o buraco relatado (a chave só existia dentro do editor,
    # e o editor só abre depois do processamento que precisa dela).
    #
    # A IA SOMA, nunca substitui. Os takes determinísticos vêm de uma ORDEM do
    # usuário — palma, ou "corta" dito — e uma resposta que não os mencione (o
    # esquema aceita remover:[]) não pode ressuscitar a tentativa que ele
    # mandou apagar. O take da IA só entra onde não há um determinístico
    # dizendo a mesma coisa.
    relatorio_ia = _cortes_da_ia(
        project, ctx, words,
        [c for c in project.analysis.get("claps", []) if c.get("enabled")],
        [a for a in project.analysis.get("whistles", []) if a.get("enabled", True)])
    vindos_da_ia: list[dict] = []
    if relatorio_ia and relatorio_ia.get("ok"):
        def _cobre(a: dict, b: dict) -> float:
            inter = min(float(a["end"]), float(b["end"])) \
                - max(float(a["start"]), float(b["start"]))
            return max(0.0, inter) / max(float(a["end"]) - float(a["start"]), 1e-9)

        vindos_da_ia = [t for t in relatorio_ia["takes"]
                        if not any(_cobre(t, d) >= 0.5 for d in takes)]
        # a decisão de restaurar sobrevive: casa por sobreposição, porque as
        # bordas do modelo mudam alguns décimos entre chamadas
        anteriores = [t for t in project.analysis.get("takes", [])
                      if t.get("restored")]
        for t in vindos_da_ia:
            for old in anteriores:
                inter = min(float(old["end"]), float(t["end"])) \
                    - max(float(old["start"]), float(t["start"]))
                uni = max(float(old["end"]), float(t["end"])) \
                    - min(float(old["start"]), float(t["start"]))
                if inter > 0 and inter / max(uni, 1e-9) >= 0.5:
                    t["restored"] = True
        # fora TODOS os takes da IA da rodada anterior, não só os de refeitura:
        # o filtro só pegava "ia" e deixava "ia_copy" para trás, então um corte
        # de copy decidido uma vez ficava para sempre — refazer a edição com
        # outro modelo, ou com a IA desligada, continuava com ele no vídeo.
        takes = sorted([t for t in takes
                        if not str(t.get("source") or "").startswith("ia")]
                       + vindos_da_ia,
                       key=lambda t: float(t["start"]))
        project.analysis["takes"] = takes
    # O RELATÓRIO DA IA fica gravado na análise e é o que o editor mostra.
    # Antes daqui só saía o campo "erro" quando faltava chave; o resto — qual
    # modelo rodou, o que ele leu do vídeo, quantos trechos tirou, quantos
    # foram RECUSADOS e por quê, quantas etapas de ritmo e marcações de câmera
    # — era calculado e jogado fora. Sem isso não há como saber se a IA botou
    # a mão no vídeo, e não saber é o mesmo que ela não ter botado.
    if relatorio_ia:
        guardado = {k: v for k, v in relatorio_ia.items() if k != "takes"}
        guardado["rodou"] = bool(relatorio_ia.get("ok"))
        guardado["aplicados"] = len(vindos_da_ia) if relatorio_ia.get("ok") else 0
    else:
        guardado = {"rodou": False, "erro": "desligada", "pulada": True}
    if not (relatorio_ia and relatorio_ia.get("ok")):
        # a IA não decidiu nada nesta rodada: o que ela decidiu na anterior não
        # pode continuar valendo em silêncio
        fora_da_ia = [t for t in takes
                      if str(t.get("source") or "").startswith("ia")]
        if fora_da_ia:
            takes = [t for t in takes
                     if not str(t.get("source") or "").startswith("ia")]
            project.analysis["takes"] = takes
            guardado["descartados_da_rodada_anterior"] = len(fora_da_ia)
    project.analysis["ai_cortes"] = guardado

    # remoções feitas à mão (pelo texto) sobrevivem à reedição automática
    manual_removed = set(project.analysis.get("manual_removed_word_ids", []))
    # a palavra de comando ("corta", "ok") é instrução, não fala: sai — mas
    # o conjunto é recalculado AQUI, respeitando enabled. O conjunto congelado
    # do analyze mantinha a palavra fora do vídeo mesmo depois de o usuário
    # desligar a bandeirinha do comando.
    from .audio.comandos import ids_de_comando as _ids_cmd
    manual_removed |= _ids_cmd(project.analysis.get("comandos", []))

    # Repetição: quando a mesma coisa é dita duas vezes, a que vale é a última.
    # Roda DEPOIS da regra do take (o que a palma já descartou não entra na
    # comparação) e ANTES do corte, para o plano já nascer sem a versão ruim.
    ctx.stage("repeticao", "procurando trechos ditos duas vezes")
    ja_fora = set(words_removed_by_takes(words, takes)) | manual_removed
    repeats = find_repeats(words, env, pause=project.plan.cut.narrative_pause,
                           already_removed=ja_fora)
    antigos = {r.get("id"): r for r in project.analysis.get("repeats", [])}
    saida = []
    for r in repeats:
        d = r.to_dict()
        # a decisão de recuperar sobrevive a uma reedição: casa pelo início
        for old_r in antigos.values():
            if abs(float(old_r.get("start", -9)) - d["start"]) < 0.2 \
                    and old_r.get("restored"):
                d["restored"] = True
        saida.append(d)
    # o usuário pode ter recuperado uma repetição que a reanálise não achou
    for old_r in project.analysis.get("repeats", []):
        if old_r.get("restored") and not any(
                abs(float(old_r.get("start", -9)) - d["start"]) < 0.2 for d in saida):
            saida.append(old_r)
    project.analysis["repeats"] = saida
    repetidas = repeats_removed_ids(saida)

    # Palma e assobio são MARCADORES: o buraco que tem um deles dentro é
    # cortado rente, sem ar. É o que dá ao usuário liberdade para demorar o
    # quanto quiser antes de recomeçar — o vazio inteiro sai.
    marcadores = [float(c["time"]) for c in project.analysis.get("claps", [])
                  if c.get("enabled")]
    marcadores += [float(a["time"]) for a in project.analysis.get("whistles", [])
                   if a.get("enabled", True)]
    # o fim de cada take descartado também é emenda de marcador
    marcadores += [float(t["end"]) for t in takes if not t.get("restored")]

    ctx.stage("cortes", "propondo cortes com encaixe no vale de energia")
    # O RITMO e a CÂMERA que a IA leu. Ela não devolve número nenhum: devolve
    # a ETAPA NARRATIVA de cada trecho e onde a câmera aperta. A velocidade
    # sai da tabela da etapa (com o teto do preset), o zoom sai da escada (com
    # o teto geométrico da resolução) e os dois sliders da primeira tela
    # continuam multiplicando por cima. Sem chave, sem internet ou com
    # resposta ruim, as listas vêm vazias e a regra do programa decide inteira.
    secoes_ia = (relatorio_ia or {}).get("secoes") or None
    camera_ia = (relatorio_ia or {}).get("camera") or None
    # A TRILHA também obedece ao que ela leu: alto no gancho, baixo embaixo da
    # explicação densa, fora em cima do preço. O ducking continua onde estava —
    # ele é reflexo (abaixa quando alguém fala); isto aqui é intenção.
    curva_musica = (relatorio_ia or {}).get("musica") or []
    if project.plan.music:
        if curva_musica:
            project.plan.music["curva"] = curva_musica
        else:
            project.plan.music.pop("curva", None)
    result = build_auto_plan(words, env, project.plan.cut, project.plan.speed,
                             takes, extra_removed=manual_removed | repetidas,
                             markers=sorted(marcadores),
                             secoes=secoes_ia, camera=camera_ia)
    plan = project.plan
    from .edit.ops import remap_output_items
    fps = project.info.fps if project.info else None
    old_tl = Timeline(plan.active_clips, fps)
    travados = _enquadramentos_travados(plan.clips)
    # marca os blocos cuja ENTRADA nasceu de um corte de copy: o zoom vai
    # abrir cena ali de propósito, para a troca de enquadramento disfarçar o
    # jump cut. Sem isso o som emenda perfeito e a cabeça pula na imagem.
    fins_de_copy = [float(t["end"]) for t in takes
                    if t.get("source") == "ia_copy" and not t.get("restored")]
    for c in result["clips"]:
        c.copy_seam = any(abs(c.src_start - f) < 0.35 for f in fins_de_copy)

    plan.clips = result["clips"]
    if not plan.clips:
        # Vídeo sem fala transcritível (b-roll, microfone mudo) — ou um teste
        # em que a única palavra dita era comando. Antes isto saía como
        # "Pronto para exportar · 0 blocos" e a exportação estourava. Regra:
        # sem fala não há o que cortar, então o vídeo fica INTEIRO, num bloco
        # só, e o aviso diz o porquê.
        from .models import Clip
        dur = float(project.info.duration if project.info else 0.0)
        if dur > 0.05:
            plan.clips = [Clip(source="main", src_start=0.0,
                               src_end=round(dur, 3))]
        ctx.progress(0.5, "não achei fala para cortar: o vídeo ficou inteiro. "
                          "Sem transcrição não há corte, legenda nem câmeras.")
    _restaurar_travados(plan.clips, travados)
    plan.removed = result["removed"]
    plan.discarded_takes = takes
    plan.claps = project.analysis.get("claps", [])
    plan.whistles = project.analysis.get("whistles", [])
    # NÃO zerar plan.subtitles aqui: os textos editados à mão são casados de
    # volta pelo rebuild (por palavra), e cutaways/overlays/desfoques são
    # reancorados pela fonte — refazer a edição não pode custar trabalho manual
    new_tl = Timeline(plan.active_clips, fps)
    remap_output_items(plan, old_tl, new_tl)
    project.analysis["removed_word_ids"] = result["removed_word_ids"]
    project.analysis["plan_notes"] = result["notes"]

    plan.repeats = saida
    ativas = [r for r in saida if not r.get("restored")]
    if ativas:
        ctx.progress(0.5, f"{len(ativas)} trecho(s) repetido(s) removido(s)")
    if secoes_ia:
        ctx.progress(0.52, f"a IA leu o vídeo em {len(secoes_ia)} etapa(s) — "
                           f"é isso que decide a velocidade de cada trecho")
    if result.get("enfatizados"):
        ctx.progress(0.53, f"câmera marcada pela IA em "
                           f"{result['enfatizados']} bloco(s)")
    ctx.progress(0.55, f"{len(plan.clips)} blocos propostos")
    ctx.stage("auditoria", "auditando as bordas de corte")
    # As bordas que dá para acertar sozinho são acertadas AQUI. O usuário
    # pediu para receber pronto: se a correção é a mesma que ele daria
    # apertando "corrigir com um clique", ela não vira pergunta.
    issues, fixed = settle_edges(plan.clips, env, words,
                                 set(result["removed_word_ids"]),
                                 markers=sorted(marcadores))
    plan.audit = issues
    plan.audit_fixed = fixed
    if fixed:
        # as bordas mudaram: o vermelho da timeline tem que acompanhar
        plan.removed = resync_removed(plan.clips, plan.removed, env.duration)

    # Jogo de zoom por ÚLTIMO: a auditoria pode desfazer um corte, e onde o
    # corte deixou de existir o enquadramento não pode mudar — senão a imagem
    # pula sem que nada tenha sido cortado, que é o defeito que o zoom existe
    # para esconder.
    ctx.stage("zoom", "montando os enquadramentos")
    resumo_zoom = recalcular_zoom(project)
    n_zoom = resumo_zoom["fechados"]

    ctx.stage("legendas", "gerando legendas")
    cues = rebuild_subtitles(project)
    project.save_analysis()
    project.save_plan()
    project.set_status("editado")
    if resumo_zoom["cenas"]:
        ctx.progress(0.95,
                     f"{resumo_zoom['cenas']} enquadramento(s), "
                     f"teto {resumo_zoom['teto']:.2f}x")
    ctx.progress(1.0, f"{len(plan.clips)} blocos, {len(cues)} legendas, "
                      + (f"{len(fixed)} borda(s) ajustada(s) sozinho, " if fixed else "")
                      + (f"{len(issues)} alerta(s) de borda" if issues
                         else "nenhum alerta de borda"))
    return {
        "clips": len(plan.clips), "subtitles": len(cues),
        "audit": audit_summary(issues),
        "audit_fixed": len(fixed),
        "repeats": len(ativas),
        "zoom": n_zoom,
        "zoom_cenas": resumo_zoom["cenas"],
        "zoom_teto": resumo_zoom["teto"],
        "duration": round(plan.duration, 2),
        "notes": result["notes"],
    }


PREVIA_ESCALA = "240"      # lado maior da prévia renderizada
PREVIA_CRF = 32
PREVIA_FPS = 30.0          # a prévia nunca precisa de mais que isto


def previa_da_edicao(project: Project, ctx) -> dict:
    """Renderiza a EDIÇÃO em 240p — a prévia que toca sem tranco nenhum.

    Medido, e o resultado inverteu o diagnóstico: baixar a resolução da cópia
    da FONTE não resolvia o travamento. A 480p ela já decodifica 122x mais
    rápido que o tempo real; a 240p vai a 150x. Resolução nunca foi o gargalo.

    O que trava é o PULO. Tocar a edição sobre o arquivo da fonte é uma busca
    por bloco (~55 ms cada), e um corte de silêncio produz um bloco a cada
    poucos segundos: tranco, tranco, tranco.

    Renderizar a edição já montada resolve por construção — o arquivo é
    LINEAR, então são ZERO buscas. Custa 7,5 s para 38 s de vídeo (5x o tempo
    real) e cabe folgado num pipeline que já gasta minutos transcrevendo. De
    quebra o zoom e a legenda vêm queimados: o que ele vê é, ao pixel, o que
    vai baixar.
    """
    from .ffmpeg_utils import em_segundo_plano

    ctx.stage("previa", "montando a prévia que toca liso")
    em_segundo_plano(True)
    try:
        return _export_previa(project, ctx)
    finally:
        em_segundo_plano(False)


def _export_previa(project: Project, ctx) -> dict:
    return export(project, ctx, {
        # SEM restart: era ele que apagava a pasta de trabalho inteira a cada
        # prévia, levando junto o cache da exportação final. O cache por hash
        # é o que faz um retoque custar segundos em vez do vídeo inteiro.
        "filename": "previa-edicao.mp4", "restart": False,
        # dentro do projeto, NÃO na pasta de Vídeos: a prévia é um arquivo de
        # trabalho de 240p e não pode ficar ao lado do vídeo final com cara
        # de ser ele.
        "output_dir": str(project.dir / "exports"),
        # 30 fps SEMPRE na prévia. Ela é 240p e serve para você ver o corte, o
        # zoom e a legenda — não há nada a perder, e num take de 60 fps isso
        # sozinho tira um terço do tempo dela (medido: 34% do trabalho da
        # cadeia inteira é decodificar e filtrar o dobro de quadros).
        "export_override": {"scale": PREVIA_ESCALA, "crf": PREVIA_CRF,
                            "preset": "ultrafast", "codec": "h264",
                            "audio_bitrate": "96k", "fps": PREVIA_FPS},
        "overwrite": True,
    })


def enriquecer(project: Project, ctx) -> dict:
    """Põe as mídias auxiliares e os cartões no lugar certo do vídeo.

    Roda dentro do clique único, DEPOIS do corte — só aí existem blocos com
    fala para ancorar um anexo. É a única chamada que manda imagem: um quadro
    de 360 px de cada mídia que VOCÊ anexou, porque sem ver o que a imagem
    mostra não há como decidir onde ela cabe. O vídeo principal nunca sai.

    Não roda à toa: sem chave, sem mídia anexada e com cartão desligado, não
    há o que pedir.
    """
    from . import db
    from .ai import gemini

    midias = [m for m in list_media(project.id)
              if m.get("kind") in ("video", "image")
              and not str(m.get("id", "")).startswith("k_")]
    quer_cartao = bool(db.get_setting("ia_cartoes", True))
    if not midias and not quer_cartao:
        return {"ok": False, "pulada": True, "motivo": "nada para posicionar"}
    if not gemini.chave_guardada():
        return {"ok": False, "pulada": True, "motivo": "sem chave do Gemini"}
    ctx.stage("anexos", f"posicionando {len(midias)} mídia(s) e escrevendo "
                        f"os cartões")
    try:
        plano = plano_da_ia(project, ctx, com_anexos=bool(midias))
    except Exception as exc:  # noqa: BLE001 — o vídeo sai sem isso
        ctx.progress(1.0, f"não deu para posicionar os anexos ({exc})")
        return {"ok": False, "erro": str(exc)}
    r = aplicar_plano_da_ia(project, plano.get("plano") or plano, so_anexos=True)
    ctx.progress(1.0, f"{len(r.get('anexos') or [])} anexo(s) e "
                      f"{len(r.get('cartoes') or [])} cartão(ões) no lugar")
    return {"ok": True, **r}


def one_click(project: Project, ctx) -> dict:
    """O clique único — TUDO antes de o editor abrir.

    O usuário solta o arquivo e recebe o vídeo PRONTO: cortado (pela IA, com
    os comandos falados e os marcadores), legendado, com o jogo de câmeras
    decidido e a prévia leve gerada. Editar é retoque, não trabalho.
    """
    ctx.progress(0.0, "iniciando")
    a = _scoped(ctx, 0.0, 0.52, lambda c: analyze(project, c))
    b = _scoped(ctx, 0.52, 0.62, lambda c: auto_edit(project, c))
    # o material auxiliar entra AQUI, depois do corte: só agora existem blocos
    # com fala para ancorar um anexo, e a duração de saída é a de verdade
    try:
        x = _scoped(ctx, 0.62, 0.68,
                    lambda c: enriquecer(project, c))
    except Exception as exc:  # noqa: BLE001 — o vídeo sai sem os anexos
        ctx.progress(0.68, f"anexos não entraram ({exc})")
        x = {"ok": False, "erro": str(exc)}
    # A CÓPIA LEVE DA FONTE saiu do caminho crítico. Ela existe para arrastar a
    # agulha sobre a fonte, e o player só cai nela enquanto a prévia da edição
    # está sendo refeita — ou seja, só depois do primeiro retoque. Gerá-la aqui
    # era um passe inteiro sobre a fonte (num 1080p60 isso é minutos) entre o
    # usuário e o vídeo dele, para um arquivo que ele talvez nunca use. Agora
    # ela é feita quando o editor precisa, em segundo plano.
    p = {"skipped": True, "detail": "a cópia leve é feita ao editar, não agora"}
    # a EDIÇÃO renderizada é o que ele assiste: linear, sem pulo, com zoom
    # e legenda queimados
    try:
        v = _scoped(ctx, 0.65, 1.0, lambda c: previa_da_edicao(project, c))
    except Exception as exc:  # noqa: BLE001
        ctx.progress(1.0, f"prévia da edição falhou ({exc})")
        v = {"ok": False}
    # O ARQUIVO FINAL não entra aqui — ele é disparado logo em seguida, por
    # baixo (server.api_oneclick). É a diferença entre esperar e receber: o
    # encode final é o passo mais caro do pipeline inteiro (medido: 0,9x tempo
    # real a 1080x1920 MAIS ~2 s fixos por trecho, e um corte de silêncio
    # produz um trecho por frase), então segurar a tela de carregamento até ele
    # terminar é minutos olhando barra depois de o vídeo já estar montado.
    # Assim o editor abre com a prévia — que é a edição renderizada, ao pixel
    # o que vai baixar — e o botão de baixar acende sozinho quando o MP4 fica
    # pronto.
    project.set_status("pronto")
    return {"analysis": a, "edit": b, "anexos": x, "proxy": p, "previa": v}


def exportar_final(project: Project, ctx) -> dict:
    """A exportação AUTOMÁTICA — a que roda por baixo e a que se refaz sozinha.

    Sempre no MESMO arquivo. A exportação manual do painel não sobrescreve de
    propósito (você pediu duas versões, recebe duas), mas esta aqui roda a cada
    retoque: sem sobrescrever, a pasta de Vídeos viraria "vsl_editado (7).mp4"
    e ninguém saberia qual é o bom.
    """
    from .ffmpeg_utils import em_segundo_plano

    em_segundo_plano(True)      # roda por baixo: cede a CPU para quem está retocando
    try:
        principal = export(project, ctx, {
            "restart": False,   # o cache por trecho é o que faz o retoque ser barato
            "overwrite": True,
            "hw_encoder": "auto",
        })
    finally:
        em_segundo_plano(False)
    # E OS FORMATOS EXTRAS, do MESMO corte. Cada um é uma geração de encode
    # A PARTIR DA FONTE, não do arquivo pronto: derivar o 16:9 do 9:16 já
    # comprimido seria esticar 1,78x em cima de artefato de compressão. O
    # recorte é concêntrico no mesmo rosto e a legenda é reescalada para o
    # quadro novo, então os três saem com a legenda no mesmo lugar relativo.
    # um extra com a MESMA proporção da gravação é o próprio vídeo principal
    # com outro nome: sai da lista antes de custar um encode inteiro
    from .render.renderer import PROPORCOES

    try:
        lw, lh = (int(x) for x in project.info.display_size)
        prop_fonte = lw / max(lh, 1e-9)
    except Exception:  # noqa: BLE001
        prop_fonte = 0.0
    extras = [e for e in (project.plan.export.extras or ())
              if e and e != "fonte"
              and abs(PROPORCOES.get(e, 0.0) - prop_fonte) > 0.01]
    saidas = [{"aspecto": "fonte", **principal}]
    base = _nome_de_arquivo(project.name)
    for i, aspecto in enumerate(extras):
        rotulo = aspecto.replace(":", "x")
        try:
            r = _scoped(ctx, 0.0, 1.0, lambda c, a=aspecto, rot=rotulo: export(
                project, c, {
                    "restart": False, "overwrite": True, "hw_encoder": "auto",
                    "filename": f"{base} {rot}.mp4",
                    "export_override": {"aspect": a},
                }))
            saidas.append({"aspecto": aspecto, **r})
        except Exception as exc:  # noqa: BLE001 — o principal já saiu
            ctx.progress(1.0, f"o formato {aspecto} falhou ({exc}); "
                              f"o principal está pronto")
    principal["formatos"] = saidas
    return principal


class _ScopedCtx:
    def __init__(self, ctx, lo: float, hi: float) -> None:
        self._ctx, self._lo, self._hi = ctx, lo, hi

    def progress(self, f: float, message: str = "", stage: str = "") -> None:
        self._ctx.progress(self._lo + (self._hi - self._lo) * f, message, stage)

    def stage(self, name: str, message: str = "") -> None:
        self._ctx.stage(name, message)

    def check(self) -> None:
        self._ctx.check()

    def cancelled(self) -> bool:
        return self._ctx.cancelled()


def _scoped(ctx, lo: float, hi: float, fn: Callable) -> Any:
    return fn(_ScopedCtx(ctx, lo, hi))


# ----------------------------------------------------------------- legendas
def corrected_words(project: Project) -> tuple[list[dict], list[dict]]:
    rules = db.list_corrections()
    return apply_corrections(project.words, rules)


def rebuild_subtitles(project: Project, timeline: Timeline | None = None) -> list[dict]:
    """Palavras corrigidas -> remapeadas -> quebradas em legendas."""
    plan = project.plan
    words, log = corrected_words(project)
    removed = set(project.analysis.get("removed_word_ids", []))
    words = [w for w in words if w.get("src_i", w["i"]) not in removed]
    fps = project.info.fps if project.info else None
    tl = timeline or Timeline(plan.active_clips, fps)
    mapped = remap_words(words, tl)
    manual = [s for s in plan.subtitles
              if s.edited or getattr(s, "start_off", 0.0)
              or getattr(s, "end_off", 0.0)]
    cues = build_cues(mapped, plan.style, limit=tl.duration)
    # word_ids em índices ORIGINAIS (src_i): os índices corrigidos renumeram
    # quando o dicionário de correções muda, e o casamento se perderia
    src_of = {w["i"]: w.get("src_i", w["i"]) for w in mapped}
    plan.subtitles = [
        Subtitle(start=c["start"], end=c["end"], text=c["text"],
                 word_ids=sorted({src_of.get(i, i) for i in c["word_ids"]
                                  if i is not None}))
        for c in cues
    ]
    # cada cue antigo editado vai para o cue novo de MAIOR sobreposição de
    # palavras. Primeiro-que-encosta dava o texto inteiro à metade errada num
    # split e descartava o segundo texto num merge.
    assignments: dict[int, list] = {}
    for old in sorted(manual, key=lambda o: o.start):
        old_ids = set(old.word_ids)
        best, best_ov = None, 0
        for ni, sub in enumerate(plan.subtitles):
            ov = len(old_ids & set(sub.word_ids))
            if ov > best_ov:
                best, best_ov = ni, ov
        if best is not None:
            assignments.setdefault(best, []).append(old)
    for ni, olds in assignments.items():
        sub = plan.subtitles[ni]
        edited_olds = [o for o in olds if o.edited]
        if edited_olds:
            # merge de dois cues editados: concatena em ordem, nunca descarta
            text = " ".join(o.text.replace("\n", " ") for o in edited_olds)
            lines = wrap(text, plan.style.max_chars_per_line,
                         plan.style.max_lines)
            sub.text = "\n".join(lines) if lines else text
            sub.edited = True
        s_off = float(getattr(olds[0], "start_off", 0.0) or 0.0)
        e_off = float(getattr(olds[-1], "end_off", 0.0) or 0.0)
        if s_off or e_off:
            sub.start = round(max(0.0, sub.start + s_off), 3)
            sub.end = round(max(sub.start + 0.2, sub.end + e_off), 3)
            sub.start_off, sub.end_off = s_off, e_off
    project.analysis["correction_log"] = log
    return [s.to_dict() for s in plan.subtitles]


def cue_list(project: Project) -> list[dict]:
    return sorted(({"start": s.start, "end": s.end, "text": s.text}
                   for s in project.plan.subtitles),
                  key=lambda c: c["start"])


# --------------------------------------------------------------- exportação
def _nome_de_arquivo(nome: str) -> str:
    """Nome de projeto -> nome de arquivo que o Windows aceita."""
    limpo = "".join(c for c in nome if c not in '\\/:*?"<>|').strip()
    return (limpo or "video") + "_editado"


def build_proxy_job(project: Project, ctx) -> dict:
    """Gera a cópia leve da fonte para a prévia (Parte: play sem engasgo)."""
    from .render.proxy import build_proxy, vale_a_pena

    if project.info is None:
        raise RuntimeError("rode a análise antes")
    precisa, motivo = vale_a_pena(project.info)
    if not precisa:
        ctx.progress(1.0, motivo)
        return {"skipped": True, "detail": motivo}
    if project.proxy_ok:
        ctx.progress(1.0, "a prévia leve já existe")
        return {"skipped": True, "detail": "proxy já existe"}
    ctx.stage("proxy", f"gerando prévia leve — {motivo}")
    res = build_proxy(project.source_path, project.proxy_file,
                      project.info.duration,
                      on_progress=lambda f: ctx.progress(f, "gerando prévia leve"),
                      cancel=ctx.cancelled)
    project.marcar_proxy()
    ctx.progress(1.0, f"prévia leve pronta: {res['width']}x{res['height']} "
                      f"a {res['fps']:.0f} fps, {res['size_bytes'] / 1e6:.1f} MB")
    return res


def export(project: Project, ctx, options: dict | None = None) -> dict:
    options = options or {}
    plan = project.plan
    if options.get("export_override"):
        # NUM PLANO À PARTE. Escrever em project.plan.export "só na memória"
        # parecia inofensivo porque o banco nunca via — mas dentro do clique
        # único a prévia de 240p roda LOGO ANTES da exportação final, no mesmo
        # objeto de projeto. O final herdava escala 240p, CRF 32 e áudio de
        # 96k: o usuário baixava a prévia achando que era o vídeo. Medido:
        # 240x426 a 0,22 Mbps no lugar de 1080x1920.
        plan = copy.copy(plan)          # raso de propósito: os blocos são os mesmos
        plan.export = ExportParams(**{**plan.export.__dict__,
                                      **options["export_override"]})
    if not plan.active_clips:
        raise RuntimeError("o plano está vazio — rode a edição automática antes")
    sources = sources_for(project)
    # A pasta de Vídeos, não a pasta de dados do app: no Windows ela fica em
    # AppData\Local, que é oculta, e o usuário exportava sem achar o arquivo.
    out_dir = Path(options["output_dir"]).expanduser() if options.get("output_dir") \
        else output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = options.get("filename") or f"{_nome_de_arquivo(project.name)}.mp4"
    dest = out_dir / Path(name).name
    if dest.exists() and not options.get("overwrite"):
        # não sobrescreve a exportação anterior em silêncio
        base, i = dest.with_suffix(""), 2
        while dest.exists():
            dest = base.with_name(f"{base.name} ({i})").with_suffix(".mp4")
            i += 1
    work = project.dir / "work"
    if options.get("restart"):
        shutil.rmtree(work, ignore_errors=True)
    hw = options.get("hw_encoder") or None
    if hw == "auto":
        # testado de verdade, não só anunciado pelo ffmpeg
        hw = hw_encoder_utilizavel()

    def cues_builder(tl: Timeline) -> list[dict]:
        rebuild_subtitles(project, tl)
        return cue_list(project)

    ctx.stage("exportando", "encodando cada trecho uma única vez")
    result = export_project(
        plan, sources, dest, work, cues_builder,
        on_progress=lambda f, m: ctx.progress(f * 0.98, m),
        cancel=ctx.cancelled, hw=hw,
    )
    # As durações medidas valeram só para esta exportação (mantê-las
    # invalidaria o cache), e o plano NÃO é regravado aqui: este é o snapshot
    # do início do job — regravá-lo apagaria qualquer edição que o usuário
    # salvou enquanto a renderização corria.
    for clip in plan.clips:
        clip.measured_duration = None
    project.set_status("exportado")
    ctx.progress(1.0, f"pronto: {dest.name}")
    payload = result.to_dict()
    payload["download"] = f"/api/projects/{project.id}/download/{dest.name}"
    return payload


def validate(project: Project, ctx, output: str | None = None) -> dict:
    # a exportação final mora na pasta de Vídeos e a prévia dentro do projeto:
    # olhar só num dos dois lugares fazia a conferência dizer "nada exportado"
    # logo depois de exportar.
    candidatos = list((project.dir / "exports").glob("*.mp4"))
    candidatos += [p for p in output_dir().glob("*.mp4")
                   if p.stem.startswith(_nome_de_arquivo(project.name))]
    exports = sorted(candidatos, key=lambda p: -p.stat().st_mtime)
    target = Path(output) if output else (exports[0] if exports else None)
    if target is None or not target.exists():
        raise RuntimeError("nenhum arquivo exportado para validar")

    words, _ = corrected_words(project)
    removed = set(project.analysis.get("removed_word_ids", []))
    tl = Timeline(project.plan.active_clips,
                  project.info.fps if project.info else None)
    expected = [w["text"] for w in words
                if w.get("src_i", w["i"]) not in removed
                and (tl.covers(float(w["start"])) or tl.covers(float(w["end"])))]

    def transcriber(path: Path) -> list[dict]:
        from .transcribe import transcribe
        from .ffmpeg_utils import extract_wav as ex_wav, read_wav_mono as rd

        tmp = project.dir / "validate.wav"
        ex_wav(path, tmp, 16000, 1)
        samples, _sr = rd(tmp)
        info = probe(path)
        res = transcribe(samples, info.duration,
                         on_progress=lambda f, m: ctx.progress(0.35 + f * 0.45, m))
        tmp.unlink(missing_ok=True)
        return res["words"]

    can_transcribe = True
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        can_transcribe = False

    expected_duration = tl.duration
    last = db.q1("SELECT result_json FROM jobs WHERE project_id=? AND kind='exportacao' "
                 "AND status='ok' ORDER BY updated_at DESC LIMIT 1", (project.id,))
    if last:
        payload = db.jloads(last["result_json"], {})
        if payload.get("output") == str(target) and payload.get("duration"):
            expected_duration = float(payload["duration"])

    report = validate_export(
        target, expected, cue_list(project), project.plan.audio,
        (project.info.v_bitrate or project.info.bitrate) if project.info else 0,
        expected_duration,
        transcriber=transcriber if can_transcribe else None,
        on_progress=lambda f, m: ctx.progress(f, m),
        work=project.dir,
    )
    report["file"] = str(target)
    return report


# -------------------------------------------------------------------- views
def build_tracks(project: "Project", blocks: list[dict],
                 duration: float) -> list[dict]:
    """As camadas da linha do tempo, cada uma com seus itens.

    Tudo isto já existia como dado solto (cutaway, sobreposição, desfoque,
    trilha). Aqui vira TRILHO: uma faixa por camada, com itens que têm começo
    e fim no tempo de SAÍDA, para poderem ser arrastados.
    """
    plan = project.plan
    midias = {m["id"]: m for m in list_media(project.id)}
    # Antes da análise a linha do tempo tem duração 0. Sem este resgate a
    # trilha entrava com começo e fim iguais e não dava para arrastar nem
    # esticar — item de largura zero não tem borda para pegar.
    if duration <= 0.01 and project.info:
        duration = float(project.info.duration or 0.0)

    def nome(mid: str) -> str:
        return (midias.get(mid) or {}).get("name", "mídia removida")

    sobreposicoes: list[dict] = []
    for c in plan.cutaways:
        if not c.enabled:
            continue
        sobreposicoes.append({
            "id": c.id, "kind": "cutaway", "label": nome(c.media_id),
            "out_start": round(c.out_start, 3), "out_end": round(c.out_end, 3),
            "media_id": c.media_id, "movable": True, "resizable": True,
            "detail": "vídeo por cima, áudio original por baixo",
        })
    for o in plan.overlays:
        if not o.enabled:
            continue
        sobreposicoes.append({
            "id": o.id, "kind": "overlay", "label": nome(o.media_id),
            "out_start": round(o.out_start, 3), "out_end": round(o.out_end, 3),
            "media_id": o.media_id, "movable": True, "resizable": True,
            "detail": "imagem/PNG por cima",
        })
    # fotos e insertos ocupam a faixa PRINCIPAL (empurram o vídeo), então
    # aparecem no trilho de vídeo, não aqui
    fotos = [b for b in blocks if b.get("kind") == "photo"
             or b.get("source") not in ("main",)]

    desfoques = [{
        "id": b.id, "kind": "blur", "label": "desfoque",
        "out_start": round(b.out_start, 3), "out_end": round(b.out_end, 3),
        "movable": True, "resizable": True,
        "detail": f"{b.shape}, força {b.strength}",
    } for b in plan.blurs if b.enabled]

    musica: list[dict] = []
    m = plan.music or {}
    if m.get("enabled") and m.get("media_id"):
        musica.append({
            "id": "music", "kind": "music", "label": nome(m.get("media_id", "")),
            "out_start": round(float(m.get("out_start", 0.0)), 3),
            "out_end": round(float(m.get("out_end") or duration), 3),
            "media_id": m.get("media_id"), "movable": True, "resizable": True,
            "gain_db": float(m.get("gain_db", -18)),
            "muted": bool(m.get("muted")),
            "ducking": bool(m.get("ducking", True)),
            "detail": ("MUDA" if m.get("muted")
                       else f"{m.get('gain_db', -18):g} dB"
                       + (", abaixa na fala" if m.get("ducking") else "")),
        })

    return [
        {"id": "V1", "label": "Vídeo", "kind": "video", "accepts": ["video", "image"],
         "items": [{"id": b["id"], "kind": b.get("kind", "speech"),
                    "label": b.get("label") or "",
                    "out_start": b.get("out_start", 0.0),
                    "out_end": b.get("out_end", 0.0),
                    "zoom": b.get("zoom", 1.0), "speed": b.get("speed", 1.0),
                    "section": b.get("section", ""), "movable": False,
                    "resizable": False}
                   for b in blocks],
         "locked": True,
         "hint": "o take principal, já cortado. Arraste as bordas vermelhas na "
                 "onda para ajustar o que saiu."},
        {"id": "V2", "label": "Sobreposição", "kind": "overlay",
         "accepts": ["video", "image"], "items": sobreposicoes,
         "hint": "vídeo ou imagem por cima do principal, por tempo determinado."},
        {"id": "FX", "label": "Desfoque", "kind": "blur", "accepts": [],
         "items": desfoques,
         "hint": "proteção de rosto e documento."},
        {"id": "A1", "label": "Trilha", "kind": "audio", "accepts": ["audio"],
         "items": musica,
         "hint": "música de fundo, com ducking automático na fala."},
    ]


def _enquadramentos_travados(clips: list) -> list[dict]:
    """Guarda o enquadramento que o usuário TRAVOU, por região da fonte.

    Refazer a edição substituía plan.clips inteiro, e com ele ia embora todo
    zoom_locked: quem travou um plano fechado num trecho perdia a decisão a
    cada reanálise, sem aviso. A âncora é o tempo na FONTE, que é o que não
    muda quando o corte muda.
    """
    return [{"a": c.src_start, "b": c.src_end, "zoom": c.zoom}
            for c in clips if c.zoom_locked and c.source == "main"
            and c.src_end > c.src_start]


def _restaurar_travados(clips: list, travados: list[dict]) -> int:
    """Devolve cada trava ao bloco novo que mais cobre a região antiga."""
    voltaram = 0
    for t in travados:
        largura = t["b"] - t["a"]
        melhor, cobertura = None, 0.0
        for c in clips:
            if c.source != "main":
                continue
            sobra = min(c.src_end, t["b"]) - max(c.src_start, t["a"])
            if sobra > cobertura:
                melhor, cobertura = c, sobra
        # metade da região antiga tem que sobreviver: menos que isso e o bloco
        # virou outra coisa, e travar o enquadramento nele seria chute
        if melhor is not None and cobertura >= largura * 0.5:
            melhor.zoom = t["zoom"]
            melhor.zoom_locked = True
            voltaram += 1
    return voltaram


# ---------------------------------------------------------------------- IA
def plano_da_ia(project: Project, ctx, com_anexos: bool = True) -> dict:
    """Pede a leitura do roteiro ao Gemini e devolve a SUGESTÃO.

    Nada é aplicado aqui. O usuário vê o que a IA propôs, com o motivo de cada
    escolha, e decide. Aplicar é outro passo, outra rota.
    """
    from . import db
    from .ai import gemini, roteiro

    chave = gemini.chave_guardada()
    if not chave:
        raise ValueError("sem chave do Gemini")
    plan = project.plan
    palavras = project.analysis.get("words") or []
    if not palavras:
        raise ValueError("rode a edição automática antes: sem transcrição a IA "
                         "não tem o que ler")

    ctx.stage("roteiro", "reunindo o texto de cada bloco")
    blocos = roteiro.blocos_do_plano(plan, palavras)
    if not blocos:
        raise ValueError("nenhum bloco com fala para a IA olhar")

    midias, quadros = [], []
    if com_anexos:
        ctx.stage("anexos", "olhando as mídias que você anexou")
        midias = [m for m in list_media(project.id)
                  if m.get("kind") in ("video", "image")]
        midias = midias[:roteiro.MAX_QUADROS]
        for m in midias:
            try:
                dur = float((m.get("info") or {}).get("duration") or 0.0)
                img, _ = video_analysis.frame_jpeg(m["path"], dur * 0.4, width=360)
                quadros.append(img)
            except Exception as exc:  # noqa: BLE001
                ctx.progress(0.2, f"não consegui ler um quadro de "
                                  f"{m.get('name', '')}: {exc}")
        # uma mídia sem quadro desalinharia a lista numerada do pedido
        midias = midias[:len(quadros)]

    ctx.stage("ia", f"perguntando ao Gemini sobre {len(blocos)} blocos")
    ctx.progress(0.35, "o vídeo NÃO é enviado — só o texto"
                       + (f" e {len(quadros)} quadro(s) dos seus anexos"
                          if quadros else ""))
    try:
        resposta = roteiro.pedir(chave, db.get_setting("gemini_model", "") or "",
                                 blocos, midias, duracao_de_saida(project),
                                 quadros)
    except gemini.ErroDaIA as exc:
        raise ValueError(str(exc)) from exc

    ctx.progress(0.9, f"o {resposta.get('_modelo', 'modelo')} respondeu")
    return {"plano": resposta, "blocos": len(blocos), "midias": len(midias),
            "modelo": resposta.get("_modelo", ""),
            "leitura": str(resposta.get("leitura", ""))[:300]}


def aplicar_plano_da_ia(project: Project, plano: dict,
                        so_anexos: bool = False) -> dict:
    """Aplica a sugestão — e recusa o que não couber, com o motivo escrito.

    ``so_anexos`` deixa etapa e ênfase quietas. No caminho automático quem já
    decidiu isso foi a leitura palavra a palavra (ai/cortes.py); esta chamada
    existe só porque ela é a que enxerga os QUADROS das mídias anexadas, e
    reaplicar as etapas por cima seria uma segunda opinião atropelando a
    primeira.
    """
    from .ai import roteiro
    from .models import Cutaway, Overlay

    if not plano:
        raise ValueError("nada para aplicar")
    plan = project.plan
    midias = [m for m in list_media(project.id)
              if m.get("kind") in ("video", "image")]
    if so_anexos:
        plano = {k: v for k, v in plano.items() if k != "blocos"}
    relatorio = roteiro.aplicar(plan, plano, midias, duracao_de_saida(project))

    for a in relatorio["anexos"]:
        if a["tipo"] == "cobertura":
            plan.cutaways.append(Cutaway(media_id=a["media_id"],
                                         out_start=a["out_start"],
                                         out_end=a["out_end"]))
        else:
            plan.overlays.append(Overlay(media_id=a["media_id"],
                                         out_start=a["out_start"],
                                         out_end=a["out_end"]))

    relatorio["cartoes"] = desenhar_cartoes(project, relatorio.get("cartoes"))

    # a etapa e a ênfase só viram imagem depois disto — e é aqui que TODAS as
    # invariantes do enquadramento são impostas, exatamente como quando quem
    # escolheu a etapa foi a regra de palavras-chave
    resumo = recalcular_zoom(project)
    project.save_plan()
    return {"ok": True, **relatorio, "zoom": resumo,
            "timeline": timeline_summary(project)}


# Onde o cartão fica no quadro. NÃO é posição fixa: é calculada para o cartão
# terminar logo ACIMA da faixa de legenda. Com um número fixo o painel batia
# em cima do texto da legenda — conferido queimando um quadro do vídeo — e a
# faixa de legenda muda de altura conforme o formato (21,5% do quadro no
# vertical, 8% no horizontal) e conforme o tamanho que o usuário escolheu.
CARTAO_RESPIRO = 0.025        # do quadro, entre o cartão e a legenda
CARTAO_TETO = 0.50            # e ele nunca sobe acima disto: ali está o rosto
CARTAO_FADE = 0.30


ESCALA_MINIMA = 0.72          # abaixo disto o texto do cartão fica pequeno demais


def posicao_do_cartao(plan, largura: int, altura: int,
                      altura_cartao: int) -> tuple[float, float]:
    """(centro Y, escala) do cartão: entre o rosto e a legenda.

    A faixa livre é estreita num vertical — o rosto termina por volta da
    metade da altura e a legenda começa a 28% do rodapé. Um cartão de três
    tópicos não cabe inteiro ali. Então ele ENCOLHE para caber, em vez de subir
    para cima da boca de quem fala: legenda menor se lê, boca coberta não tem
    conserto. Abaixo de 72% o texto fica pequeno demais e aí o cartão sobe
    mesmo — nunca por cima da legenda, que é a regra dura.
    """
    st = plan.style
    topo_da_legenda = altura - (st.margin_v
                                + st.fontsize * min(2, st.max_lines or 2))
    base = topo_da_legenda - altura * CARTAO_RESPIRO
    faixa = base - altura * CARTAO_TETO
    escala = 1.0
    if altura_cartao > faixa > 0:
        escala = max(ESCALA_MINIMA, faixa / altura_cartao)
    centro = (base - altura_cartao * escala / 2.0) / max(altura, 1)
    return max(0.0, min(0.92, centro)), round(escala, 4)


def desenhar_cartoes(project: Project, pedidos: list[dict] | None) -> list[dict]:
    """Desenha cada cartão e o põe na linha do tempo como sobreposição.

    O PNG é nomeado pelo CONTEÚDO, então refazer a edição com o mesmo texto
    reaproveita o arquivo — e o cache de trechos do render não é invalidado à
    toa. Os cartões da rodada anterior saem antes: quem escreveu foi a IA, e
    se ela mudou de ideia o painel velho não pode ficar.
    """
    from .models import Overlay
    from .render import cartao as cartao_mod

    dir_cartoes = project.dir / "cartoes"
    plan = project.plan
    antigos = {o.media_id for o in plan.overlays if str(o.media_id).startswith("k_")}
    plan.overlays = [o for o in plan.overlays
                     if not str(o.media_id).startswith("k_")]
    for mid in antigos:
        db.ex("DELETE FROM media WHERE id=? AND project_id=?", (mid, project.id))
    if not pedidos:
        return []

    try:
        lv, av = (int(x) for x in project.info.display_size)
    except Exception:  # noqa: BLE001
        return []

    feitos: list[dict] = []
    for c in pedidos:
        nome = cartao_mod.nome_do_cartao(c["titulo"], c.get("topicos"),
                                         c.get("numero", ""), lv, av)
        destino = dir_cartoes / nome
        try:
            if not destino.exists():
                cartao_mod.desenhar(
                    destino, lv, av,
                    titulo=c["titulo"], topicos=c.get("topicos"),
                    numero=c.get("numero", ""), fonte=plan.style.font)
            info = probe(destino).to_dict()
        except Exception as exc:  # noqa: BLE001 — um cartão ruim não derruba o vídeo
            feitos.append({**c, "erro": str(exc)})
            continue
        mid = f"k_{uuid.uuid4().hex[:8]}"
        db.ex("INSERT INTO media(id, project_id, path, kind, name, info_json, "
              "descricao, created_at) VALUES (?,?,?,?,?,?,?,?)",
              (mid, project.id, str(destino), "image",
               f"cartão: {c['titulo'][:40]}", db.jdumps(info),
               "cartão escrito pela IA e desenhado pelo programa", time.time()))
        cy, escala = posicao_do_cartao(plan, lv, av, int(info.get("height") or 0))
        plan.overlays.append(Overlay(
            media_id=mid, out_start=c["out_start"], out_end=c["out_end"],
            x=0.5, y=cy, scale=escala, opacity=1.0,
            anim_in="fade", anim_out="fade",
            dur_in=CARTAO_FADE, dur_out=CARTAO_FADE))
        feitos.append({**c, "media_id": mid, "arquivo": destino.name})
    return feitos


def recalcular_zoom(project: Project) -> dict:
    """Refaz os enquadramentos — SEMPRE com o teto da resolução da fonte.

    Chamar assign_zoom sem as larguras faz o teto virar params.max_zoom, e a
    invariante que impede o recorte de pedir mais pixels do que a fonte tem
    é contornada em silêncio: numa fonte 720p para saída 1080p o teto certo é
    1,00 e a chamada curta entrega 1,15. Existiam dois caminhos assim
    (/api/params e a troca de etapa); agora existe um só, e é este.
    """
    from .render.renderer import target_size

    plan = project.plan
    largura_fonte = (project.info.display_size[0] if project.info else 0)
    largura_saida = (target_size(project.info, plan.export)[0]
                     if project.info else largura_fonte)
    resumo = assign_zoom(plan.clips, plan.zoom, largura_fonte, largura_saida)
    plan.zoom_audit = zoom_auditar(plan.clips, plan.zoom, resumo["teto"])
    return resumo


def teto_de_zoom(project: Project) -> float:
    """O maior enquadramento que a fonte aguenta sem inventar pixel."""
    from .edit.zoom import zoom_maximo
    from .render.renderer import target_size

    largura_fonte = (project.info.display_size[0] if project.info else 0)
    largura_saida = (target_size(project.info, project.plan.export)[0]
                     if project.info else largura_fonte)
    return zoom_maximo(largura_fonte, largura_saida, project.plan.zoom.max_zoom)


def duracao_de_saida(project: Project) -> float:
    """Quanto dura o vídeo montado — 0 quando ainda não há plano nenhum.

    Antes da análise não existe bloco, e a duração do resumo é 0: sem o recuo
    para a duração da fonte, todo anexo cairia "fora do vídeo".
    """
    d = float(timeline_summary(project).get("duration") or 0.0)
    if d <= 0.01 and project.info:
        d = float(project.info.duration or 0.0)
    return d


def timeline_summary(project: Project) -> dict:
    plan = project.plan
    tl = Timeline(plan.active_clips, project.info.fps if project.info else None)
    blocks = []
    for placed in tl:
        c = placed.clip
        blocks.append({
            **c.to_dict(),
            "out_start": round(placed.out_start, 4),
            "out_end": round(placed.out_end, 4),
        })
    return {
        "duration": round(tl.duration, 3),
        "source_duration": round(project.analysis.get("duration", 0.0), 3),
        "blocks": blocks,
        "tracks": build_tracks(project, blocks, round(tl.duration, 3)),
        "removed": [r.to_dict() for r in plan.removed],
        "takes": plan.discarded_takes,
        "claps": plan.claps,
        "whistles": plan.whistles,
        "subtitles": [s.to_dict() for s in plan.subtitles],
        "audit": plan.audit,
        "audit_fixed": plan.audit_fixed,
        "repeats": plan.repeats,
        "zoom_scenes": zoom_cenas(plan.clips),
        "zoom_audit": plan.zoom_audit,
        "look": plan.look,
        "look_vignette": plan.look_vignette,
        "word_fixes": project.analysis.get("word_fixes", []),
        "zoom": {"enabled": plan.zoom.enabled,
                 "ladder": list(plan.zoom.ladder),
                 "seconds_per_scene": plan.zoom.seconds_per_scene,
                 "amplitude": plan.zoom.amplitude,
                 "max_zoom": plan.zoom.max_zoom,
                 "intensity": plan.zoom.intensity,
                 "face_x": plan.zoom.face_x, "face_y": plan.zoom.face_y,
                 "face_method": plan.zoom.face_method,
                 "anchor_x": plan.zoom.anchor_x, "anchor_y": plan.zoom.anchor_y,
                 "unsharp": plan.zoom.unsharp},
        "cutaways": [c.to_dict() for c in plan.cutaways],
        "overlays": [o.to_dict() for o in plan.overlays],
        "blurs": [b.to_dict() for b in plan.blurs],
        "speed_warn": [b["id"] for b in blocks
                       if b["speed"] > plan.speed.warn_above],
    }
