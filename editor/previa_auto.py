"""A PRÉVIA COM TODAS AS ANIMAÇÕES, sempre em dia — refeita pelo servidor.

"Quero poder ver na prévia todas as animações." A prévia renderizada sempre
trouxe tudo (gráficos, cenas, logos, transições, camadas); o que falhava era
ela chegar à tela: o editor só a pegava no fim do clique único (e perdia a
corrida com a exportação final que entra na fila antes), não a buscava ao
reabrir o projeto, ignorava a pós feita pelo Claude e nunca sabia de uma
mudança feita pelo MCP.

Agora quem manda é o plano: a cada plano gravado — pela tela, pelo Claude,
pelo MCP — o servidor espera uns segundos parado e refaz a prévia (só os
trechos que mudaram; o resto vem do cache). O editor ouve o fim do trabalho
"previa" e troca o vídeo.

Não roda enquanto o clique único ou o Claude estão trabalhando: eles gravam
o plano dezenas de vezes e fazem a prévia deles no fim.
"""
from __future__ import annotations

import os
import threading

ESPERA = 3.5
# enquanto um destes roda, quem faz a prévia é ele, no fim
_DONOS_DA_PREVIA = ("clique-unico", "claude", "pacote", "analise", "edicao", "diretor")

_trava = threading.Lock()
_relogios: dict[str, threading.Timer] = {}


def ligado() -> bool:
    return os.environ.get("SHARKCUT_PREVIA_AUTO", "1") != "0"


def plano_mudou(pid: str) -> None:
    """Reinicia a espera deste projeto (debounce)."""
    if not ligado():
        return
    with _trava:
        velho = _relogios.pop(pid, None)
        if velho:
            velho.cancel()
        t = threading.Timer(ESPERA, _disparar, args=(pid,))
        t.daemon = True
        _relogios[pid] = t
        t.start()


def _disparar(pid: str) -> None:
    from . import projects as svc
    from .jobs import get_queue

    with _trava:
        _relogios.pop(pid, None)
    fila = get_queue()
    vivos = [j for j in fila.list(pid) if j.status in ("fila", "rodando")]
    if any(j.kind in _DONOS_DA_PREVIA for j in vivos):
        return
    if any(j.kind == "previa" for j in vivos):
        plano_mudou(pid)            # termina a que está rodando e tenta de novo
        return
    try:
        p = svc.load(pid)
    except KeyError:
        return
    if not p.plan.active_clips or svc.estado_da_previa(p)["em_dia"]:
        return
    fila.submit("previa", pid, lambda ctx: svc.previa_da_edicao(svc.load(pid), ctx),
                paralelo=True)
