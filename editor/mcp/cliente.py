"""Conversa com o editor que está rodando na máquina.

Sem dependência nova: urllib da biblioteca padrão. O editor tem cinco
dependências no total e o instalador dele já briga por espaço em disco — um
pacote a mais aqui custa megabytes na máquina de quem só quer cortar um vídeo.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class EditorFora(Exception):
    """O editor não está rodando (ou não respondeu a tempo)."""


class ErroDoEditor(Exception):
    """O editor respondeu, e respondeu que não."""


# estados finais de um trabalho; os em inglês ficam por garantia
FINAIS = ("ok", "erro", "cancelado", "done", "error", "cancelled")


class Cliente:
    """Cliente HTTP do editor local.

    ``transporte`` existe para o teste: passando um objeto com ``request`` no
    molde do TestClient do FastAPI, o MCP inteiro roda sem abrir socket nenhum.
    """

    def __init__(self, base: str = "", transporte=None, timeout: float = 120.0):
        from ..config import PORT

        self.base = (base or f"http://127.0.0.1:{PORT}").rstrip("/")
        self.transporte = transporte
        self.timeout = timeout

    # ------------------------------------------------------------- chamadas
    def pedir(self, metodo: str, rota: str, corpo: dict | None = None,
              params: dict | None = None, timeout: float | None = None):
        if params:
            rota = f"{rota}?{urllib.parse.urlencode(params)}"
        if self.transporte is not None:
            return self._pelo_transporte(metodo, rota, corpo)
        url = f"{self.base}{rota}"
        dados = None
        cabecalhos = {"Accept": "application/json"}
        if corpo is not None:
            dados = json.dumps(corpo).encode("utf-8")
            cabecalhos["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=dados, headers=cabecalhos,
                                     method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                bruto = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detalhe = exc.read().decode("utf-8", "replace")[:400]
            raise ErroDoEditor(self._motivo(detalhe, exc.code)) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise EditorFora(
                "o editor não respondeu em 127.0.0.1. Abra o Sharkcut "
                "(iniciar.bat) e tente de novo."
            ) from exc
        return json.loads(bruto) if bruto.strip() else {}

    def _pelo_transporte(self, metodo: str, rota: str, corpo: dict | None):
        r = self.transporte.request(metodo, rota, json=corpo)
        if r.status_code >= 400:
            raise ErroDoEditor(self._motivo(r.text, r.status_code))
        return r.json() if r.text.strip() else {}

    @staticmethod
    def _motivo(bruto: str, codigo: int) -> str:
        try:
            d = json.loads(bruto)
            if isinstance(d, dict) and d.get("detail"):
                return str(d["detail"])
        except Exception:  # noqa: BLE001
            pass
        return f"o editor recusou (HTTP {codigo})"

    def get(self, rota: str, **params):
        return self.pedir("GET", rota, params=params or None)

    def post(self, rota: str, corpo: dict | None = None, timeout: float | None = None):
        return self.pedir("POST", rota, corpo if corpo is not None else {},
                          timeout=timeout)

    def put(self, rota: str, corpo: dict | None = None):
        return self.pedir("PUT", rota, corpo if corpo is not None else {})

    # ---------------------------------------------------------------- jobs
    def esperar_job(self, pid: str, job_id: str, limite: float = 3600.0,
                    passo: float = 1.0, relogio=time.monotonic,
                    dormir=time.sleep) -> dict:
        """Espera um trabalho terminar e devolve o registro dele.

        A API do editor não tem rota de UM job nem espera síncrona: o que
        existe é a lista por projeto. Então é sondagem mesmo — e com limite,
        porque um render de uma hora existe e travar a conversa para sempre é
        pior que avisar que ainda está rodando.
        """
        fim = relogio() + limite
        ultimo: dict = {}
        while relogio() < fim:
            for j in self.get("/api/jobs", project_id=pid):
                if j.get("id") == job_id:
                    ultimo = j
                    break
            estado = ultimo.get("status")
            # os estados do editor são em PORTUGUÊS (ok, erro, cancelado —
            # editor/jobs.py). Esperar por "done" fazia toda ferramenta que
            # roda um trabalho (editar sozinho, exportar, acrescentar vídeo)
            # esperar a hora inteira do limite com o trabalho já pronto.
            if estado in FINAIS:
                return ultimo
            # Job que some da lista terminou ANTES de a primeira sondagem
            # chegar, ou o editor reiniciou: a lista de /api/jobs vive em
            # memória, não na tabela. Nos dois casos, insistir não ajuda.
            if ultimo and estado is None:
                return ultimo
            dormir(passo)
        ultimo.setdefault("status", "rodando")
        ultimo["_estourou"] = True
        return ultimo
