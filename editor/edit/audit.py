"""Auditoria automática de bordas (Parte 3.5).

Depois de montar o plano, toda borda de corte REAL é conferida contra o
envelope. Borda em cima de fala (acima de piso + 25 dB) vira alerta com
correção de um clique.

Ponto de subdivisão — onde o áudio continua e só muda a velocidade — não é
corte e não deve alarmar.
"""
from __future__ import annotations

from ..audio.envelope import Envelope
from ..models import Clip
from .snap import snap_end, snap_start


def audit_edges(clips: list[Clip], env: Envelope, words: list[dict],
                removed_ids: set[int] | None = None) -> list[dict]:
    removed_ids = removed_ids or set()
    issues: list[dict] = []
    active = [c for c in clips if c.enabled and c.source == "main"]

    for i, clip in enumerate(active):
        prev = active[i - 1] if i else None
        nxt = active[i + 1] if i + 1 < len(active) else None
        contiguous_in = bool(prev and prev.source == clip.source
                             and abs(prev.src_end - clip.src_start) < 0.002)
        contiguous_out = bool(nxt and nxt.source == clip.source
                              and abs(clip.src_end - nxt.src_start) < 0.002)

        if not contiguous_in and clip.src_start > 0.03:
            issues.extend(_check(env, clip, "in", clip.src_start, words, removed_ids))
        if not contiguous_out and clip.src_end < env.duration - 0.03:
            issues.extend(_check(env, clip, "out", clip.src_end, words, removed_ids))
    return issues


def _check(env: Envelope, clip: Clip, side: str, t: float, words: list[dict],
           removed_ids: set[int]) -> list[dict]:
    out: list[dict] = []
    level = env.value_at(t)
    if level > env.audit_threshold:
        fix = (snap_start(env, t) if side == "in" else snap_end(env, t))
        out.append({
            "clip_id": clip.id, "side": side, "time": round(t, 4),
            "severity": "alto",
            "level_db": round(level, 2),
            "threshold_db": round(env.audit_threshold, 2),
            "message": (f"borda de corte em {t:.2f} s está a "
                        f"{level:.1f} dB (limiar {env.audit_threshold:.1f} dB): "
                        f"está cortando fala"),
            "suggestion": fix.time,
            "suggestion_reason": fix.reason,
        })

    # checagem independente do envelope: a borda parte uma palavra preservada?
    # Só vale se houver SOM ali: uma palavra cujo intervalo cobre silêncio é
    # erro de alinhamento do Whisper, não fala sendo cortada. Sem esta trava,
    # cada corte de silêncio feito por dentro de uma palavra esticada virava
    # um alerta — 117 deles numa gravação de 11 minutos.
    if level <= env.silence_threshold:
        return out
    for w in words:
        if w["i"] in removed_ids:
            continue
        if w["start"] + 0.02 < t < w["end"] - 0.02:
            out.append({
                "clip_id": clip.id, "side": side, "time": round(t, 4),
                "severity": "alto",
                "level_db": round(level, 2),
                "threshold_db": round(env.audit_threshold, 2),
                "message": (f'borda em {t:.2f} s cai dentro da palavra '
                            f'"{w["text"]}" ({w["start"]:.2f}–{w["end"]:.2f} s)'),
                "suggestion": (w["start"] - 0.05 if side == "in" else w["end"] + 0.05),
                "suggestion_reason": "mover a borda para fora da palavra",
                "word": w["text"],
            })
            break
    return out


def audit_summary(issues: list[dict]) -> dict:
    return {
        "total": len(issues),
        "high": sum(1 for i in issues if i.get("severity") == "alto"),
        "ok": not issues,
    }


def apply_fix(clips: list[Clip], issue: dict,
              words: list[dict] | None = None,
              removed_ids: set[int] | None = None) -> bool:
    """Aplica a correção sugerida de um alerta (o clique único da interface).

    A sugestão vem de um snap sem contexto de vizinhança — clampada aqui:
    a borda nunca invade o clipe vizinho (mesmo trecho da fonte encodado duas
    vezes) nem uma palavra removida pelo usuário (restauraria o removido).
    """
    words = words or []
    removed_ids = removed_ids or set()
    for clip in clips:
        if clip.id != issue.get("clip_id"):
            continue
        t = float(issue["suggestion"])
        same = [c for c in clips if c.enabled and c.source == clip.source
                and c is not clip]
        if issue["side"] == "in":
            prev_end = max((c.src_end for c in same
                            if c.src_end <= clip.src_start + 1e-6), default=0.0)
            t = max(t, prev_end)
            for w in words:
                if w["i"] not in removed_ids:
                    continue
                # antes ou ATRAVESSANDO a borda: recuar para dentro dela
                # restauraria a palavra removida
                if w["start"] < clip.src_start + 1e-6 and t < w["end"]:
                    t = max(t, w["end"] + 0.02)
            if t < clip.src_end - 0.05:
                clip.src_start = round(t, 4)
                return True
        else:
            next_start = min((c.src_start for c in same
                              if c.src_start >= clip.src_end - 1e-6),
                             default=1e12)
            t = min(t, next_start)
            for w in words:
                if w["i"] not in removed_ids:
                    continue
                if w["end"] > clip.src_end - 1e-6 and t > w["start"]:
                    t = min(t, w["start"] - 0.02)
            if t > clip.src_start + 0.05:
                clip.src_end = round(t, 4)
                return True
    return False


def _edge_ok(env: Envelope, t: float, words: list[dict],
             removed_ids: set[int]) -> bool:
    """A borda está limpa? Nível abaixo do limiar e fora de palavra viva."""
    if env.value_at(t) > env.audit_threshold:
        return False
    for w in words:
        if w["i"] in removed_ids:
            continue
        if w["start"] + 0.02 < t < w["end"] - 0.02:
            return False
    return True


MAX_GIVE_UP = 0.35      # quanto de conteúdo uma correção pode custar


def settle_edges(clips: list[Clip], env: Envelope, words: list[dict],
                 removed_ids: set[int] | None = None,
                 rounds: int = 3) -> tuple[list[dict], list[dict]]:
    """Resolve sozinho as bordas que dá para resolver.

    O usuário não quer clicar em "corrigir com um clique" quatro vezes antes
    de exportar: se a correção é a mesma que ele daria — encaixar a borda no
    vale, ou abrir a borda para caber a palavra inteira — ela é aplicada
    aqui. Sobra na lista só o que exige decisão de verdade: borda no meio de
    fala contínua, onde qualquer escolha come palavra.

    Devolve (alertas que sobraram, correções aplicadas).
    """
    removed_ids = removed_ids or set()
    applied: list[dict] = []
    issues = audit_edges(clips, env, words, removed_ids)
    for _ in range(rounds):
        if not issues:
            break
        moved = False
        for issue in issues:
            clip = next((c for c in clips if c.id == issue.get("clip_id")), None)
            if clip is None:
                continue
            before = (clip.src_start, clip.src_end)
            if not apply_fix(clips, issue, words, removed_ids):
                continue
            side = issue["side"]
            new_t = clip.src_start if side == "in" else clip.src_end
            # encolher custa conteúdo; abrir a borda não custa nada
            give_up = (new_t - before[0]) if side == "in" else (before[1] - new_t)
            if give_up > MAX_GIVE_UP or not _edge_ok(env, new_t, words, removed_ids):
                clip.src_start, clip.src_end = before      # não melhorou: desfaz
                continue
            applied.append({
                "clip_id": clip.id, "side": side,
                "from": round(issue["time"], 3), "to": round(new_t, 3),
                "reason": issue.get("suggestion_reason", ""),
                "message": issue.get("message", ""),
            })
            moved = True
        issues = audit_edges(clips, env, words, removed_ids)
        if not moved:
            break

    # Última cartada: a borda que não dá para limpar não vira pergunta — o
    # corte ali simplesmente não acontece. A pausa fica no vídeo, e a regra
    # que importa ("corte não pode comer palavra") continua de pé. Perder
    # meio segundo de pausa é muito melhor do que perder meia palavra.
    for issue in list(issues):
        undone = _uncut(clips, env, words, removed_ids, issue)
        if undone:
            applied.append(undone)
    issues = audit_edges(clips, env, words, removed_ids)

    # Nada vira pergunta. Sobrou borda em fala contínua onde nem desfazer o
    # corte resolve (o buraco tem palavra removida dentro)? Então ela vai para
    # o MENOS RUIM lugar possível — o ponto de menor energia por perto, que é
    # exatamente onde o usuário poria. Perguntar "escolha você" não é
    # automação; é passar a conta.
    for issue in list(issues):
        movido = _least_bad(clips, env, words, removed_ids, issue)
        if movido:
            applied.append(movido)
    issues = audit_edges(clips, env, words, removed_ids)
    return issues, applied


LEAST_BAD_WINDOW = 0.40


def _least_bad(clips: list[Clip], env: Envelope, words: list[dict],
               removed_ids: set[int], issue: dict) -> dict | None:
    """Move a borda para o ponto de menor energia da vizinhança.

    Último recurso, quando não há vale nenhum: em vez de deixar a borda em
    cima de uma vogal aberta, põe no ponto mais fraco que existe por perto.
    Continua cortando fala — mas na consoante, não no meio do "aaaa".
    """
    clip = next((c for c in clips if c.id == issue.get("clip_id")), None)
    if clip is None:
        return None
    side = issue["side"]
    t0 = clip.src_start if side == "in" else clip.src_end
    outros = [c for c in clips if c.enabled and c.source == clip.source
              and c is not clip]
    if side == "in":
        piso = max((c.src_end for c in outros if c.src_end <= t0 + 1e-6), default=0.0)
        teto = clip.src_end - 0.20
    else:
        piso = clip.src_start + 0.20
        teto = min((c.src_start for c in outros if c.src_start >= t0 - 1e-6),
                   default=env.duration)
    for w in words:
        if w["i"] not in removed_ids:
            continue
        if side == "in" and w["start"] < clip.src_start + 1e-6:
            piso = max(piso, w["end"] + 0.02)
        if side == "out" and w["end"] > clip.src_end - 1e-6:
            teto = min(teto, w["start"] - 0.02)
    lo = max(piso, t0 - LEAST_BAD_WINDOW)
    hi = min(teto, t0 + LEAST_BAD_WINDOW)
    if hi - lo < 0.04:
        return None
    i0, i1 = env.slice_indices(lo, hi)
    if i1 <= i0:
        return None
    import numpy as np

    melhor = int(np.argmin(env.db[i0:i1])) + i0
    t = env.time(melhor)
    if abs(t - t0) < 0.005:
        return None
    if side == "in":
        clip.src_start = round(t, 4)
    else:
        clip.src_end = round(t, 4)
    return {
        "clip_id": clip.id, "side": side,
        "from": round(t0, 3), "to": round(t, 3),
        "reason": (f"sem vale por perto: borda posta no ponto mais fraco "
                   f"({env.value_at(t):.1f} dB)"),
        "message": issue.get("message", ""),
        "kind": "menos-ruim",
    }


def _uncut(clips: list[Clip], env: Envelope, words: list[dict],
           removed_ids: set[int], issue: dict) -> dict | None:
    """Desfaz o corte de silêncio adjacente a uma borda suja, se der.

    Só quando o buraco é silêncio puro: se houver palavra lá dentro (removida
    de propósito ou engolida por um take), fechar o buraco traria a fala de
    volta, e isso o usuário não pediu.
    """
    main = sorted([c for c in clips if c.enabled and c.source == "main"],
                  key=lambda c: c.src_start)
    idx = next((i for i, c in enumerate(main) if c.id == issue.get("clip_id")), None)
    if idx is None:
        return None
    clip = main[idx]
    if issue["side"] == "in":
        left, right = (main[idx - 1] if idx else None), clip
    else:
        left, right = clip, (main[idx + 1] if idx + 1 < len(main) else None)
    if left is None or right is None:
        return None
    a, b = left.src_end, right.src_start
    if b - a <= 0.02 or b - a > 6.0:
        return None
    for w in words:
        if min(w["end"], b) - max(w["start"], a) > 0.02:
            return None                 # tem palavra no buraco: não fecha
    left.src_end = round(b, 4)          # os dois se encontram: vira contíguo
    return {
        "clip_id": clip.id, "side": issue["side"],
        "from": round(issue["time"], 3), "to": round(b, 3),
        "reason": "corte desfeito: a pausa fica no vídeo",
        "message": issue.get("message", ""),
        "kind": "sem-corte",
    }
