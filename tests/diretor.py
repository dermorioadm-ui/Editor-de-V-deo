"""Direção e Codex: contratos, estado real, isolamento, falhas e pixels.

    python -m tests.diretor
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TMP = tempfile.TemporaryDirectory(prefix="sharkcut-diretor-", ignore_cleanup_errors=True)
os.environ["EDITOR_DATA_DIR"] = str(Path(TMP.name) / "dados")
os.environ["EDITOR_OUTPUT_DIR"] = str(Path(TMP.name) / "saida")

import numpy as np
from fastapi.testclient import TestClient
from editor import diretor as D, editores as E, codex_editor as X, claude_editor as C, projects as S
from editor.models import EditPlan, Clip, Grafico
from editor.mcp.cliente import Cliente
from editor.mcp import ferramentas as F
from editor.mcp.__main__ import processar
from editor.render import animacao as A, motion as M
from editor.server import app
from editor.config import FFMPEG
from tests.mcp import semear


class Contexto:
    def stage(self, *a): pass
    def progress(self, *a): pass
    def cancelled(self): return False


class DiretorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tc = TestClient(app)
        cls.c = Cliente(transporte=cls.tc, origem="codex")
        cls.pid = semear(cls.c, Path(TMP.name))
        cls.inicial = S.load(cls.pid).plan.to_dict()

    def setUp(self):
        self.p = S.load(self.pid)
        self.p.plan = EditPlan.from_dict(self.inicial)
        self.p.plan.marca = "-"
        self.p.analysis.pop("direcao", None)
        self.p.analysis.pop("direcao_quadros", None)
        self.p.save_plan(); self.p.save_analysis()

    def planejar(self):
        return D.planejar(self.p, {"objetivo": "Explicar uma ideia", "linguagem": "editorial",
            "momentos": [{"inicio": 0, "fim": 3, "fala": "alfa bravo", "intencao": "gancho claro"}]})

    def test_migracao_e_etapas_independentes(self):
        p = EditPlan.from_dict({"editor": "claude", "pos_claude": True})
        self.assertEqual(E.acabamento(p), "claude")
        p = EditPlan.from_dict({"editor": "codex", "pos_claude": True, "pos_editor": ""})
        self.assertEqual(p.editor, "codex"); self.assertEqual(E.acabamento(p), "")
        p.pos_editor = "claude"
        self.assertEqual(E.acabamento(EditPlan.from_dict(p.to_dict())), "claude")
        self.assertFalse(S.gemini_permitido(type("P", (), {"plan": p})()))

    def test_plano_evidencia_e_revisao_vencida(self):
        est = self.planejar()
        with self.assertRaises(ValueError): D.revisar(self.p, "Conferi título e legenda em todos os momentos.")
        D.registrar_quadros(self.p, est["tempos_conferir"])
        self.assertTrue(D.revisar(self.p, "Conferi título e legenda em todos os momentos.")["aprovada"])
        self.p.plan.graficos.append(Grafico(texto="Mudou", out_start=0, out_end=3))
        self.assertFalse(D.estado(self.p)["aprovada"])
        self.assertEqual(len(D.estado(self.p)["faltam_quadros"]), 3)

    def test_tempos_invalidos_nao_apagam_plano(self):
        self.planejar()
        antes = self.p.analysis["direcao"].copy()
        for t in [float("nan"), -1, 99999]:
            with self.assertRaises(ValueError):
                D.planejar(self.p, {"objetivo": "x", "linguagem": "x", "momentos": [
                    {"inicio": t, "fim": 3, "fala": "x", "intencao": "x"}]})
        self.assertEqual(self.p.analysis["direcao"], antes)

    def test_revisao_invalida_quando_texto_muda_e_ignora_cache_de_cues(self):
        est = self.planejar()
        sig = D.assinatura(self.p)
        S.rebuild_subtitles(self.p)
        self.assertEqual(sig, D.assinatura(self.p))
        D.registrar_quadros(self.p, est["tempos_conferir"])
        D.revisar(self.p, "Conferi os títulos e as legendas renderizadas.")
        self.p.analysis["words"][0]["text"] = "outra palavra"
        self.assertFalse(D.estado(self.p)["aprovada"])
        self.assertNotEqual(sig, D.assinatura(self.p))

    def test_etapas_independentes_e_falha_interrompe_finalizacao(self):
        for inicial, pos, esperado in [("codex", "codex", [("codex", "completo")]),
                ("codex", "claude", [("codex", "edicao"), ("claude", "pos")]),
                ("", "codex", [("codex", "pos")]), ("codex", "", [("codex", "edicao")])]:
            self.p.plan.editor, self.p.plan.pos_editor = inicial, pos
            self.p.save_plan()
            chamadas = []
            def executar(pid, ctx, provedor, modo):
                chamadas.append((provedor, modo))
                return {"ok": True}
            with patch.object(S, "one_click", return_value={}), patch.object(S, "_scoped", side_effect=lambda c, a, b, fn: fn(c)), \
                 patch.object(E, "executar", side_effect=executar):
                E.pipeline(self.pid, Contexto(), [])
            self.assertEqual(chamadas, esperado)
        with patch.object(S, "one_click", return_value={}), patch.object(S, "_scoped", side_effect=lambda c, a, b, fn: fn(c)), \
             patch.object(E, "executar", return_value={"ok": False, "erro": "sem revisão"}):
            with self.assertRaisesRegex(RuntimeError, "sem revisão"):
                E.pipeline(self.pid, Contexto(), [])

    def test_composicao_real_autoria_e_remocao(self):
        manual = Grafico(texto="Manual", origem="manual", out_start=0, out_end=3)
        claude = Grafico(texto="Claude", origem="claude", out_start=0, out_end=3)
        self.p.plan.graficos += [manual, claude]; self.p.save_plan()
        r = F.chamar(self.c, "compor", {"projeto": self.pid, "tipo": "gancho", "inicio": 0,
            "fim": 3, "titulo": "UMA IDEIA", "estilo": "marca"})
        self.assertIn("ids", r)
        g = S.load(self.pid).plan.graficos[-1]
        self.assertEqual((g.origem, g.entrada, g.estilo), ("codex", "linhas", "vidro"))
        F.chamar(self.c, "tirar_da_pos", {"projeto": self.pid, "tudo": True})
        self.assertEqual([g.id for g in S.load(self.pid).plan.graficos], [manual.id, claude.id])

    def test_composicao_invalida_atomica(self):
        antes = self.p.plan.to_dict()
        with self.assertRaises(ValueError):
            D.compor(self.p, {"tipo": "passos", "inicio": 0, "fim": 3, "titulo": "PASSOS",
                "itens": [{"texto": "um", "em": 0}, {"texto": "dois", "em": 9}]})
        self.assertEqual(self.p.plan.to_dict(), antes)

    def test_comparacao_em_duas_colunas_e_tempo_da_fala(self):
        D.compor(self.p, {"tipo": "comparacao", "inicio": 0, "fim": 5, "titulo": "ESCOLHA",
            "rotulos": ["OPÇÃO A", "OPÇÃO B"], "itens": [
                {"texto": "MENOS RUÍDO", "em": 0.3}, {"texto": "MAIS CLAREZA", "em": 2}]})
        g = self.p.plan.graficos[-1]
        self.assertEqual(g.tipo, "comparacao")
        for W, H in [(360, 640), (640, 360), (400, 400)]:
            els = M.elementos(g, W, H)
            lados = [e for e in els if any("MENOS" in str(v) or "MAIS" in str(v) for v in e.variantes)]
            self.assertEqual(len(lados), 2)
            self.assertLess(lados[0].x, lados[1].x)
            self.assertAlmostEqual(lados[1].aparece, 2.08)
            self.assertTrue(all(0 <= e.x <= W and 0 <= e.y <= H for e in els))

    def test_mcp_etapa_e_projeto_aplicados_no_servidor(self):
        c = Cliente(transporte=self.tc, origem="codex", projeto=self.pid, permitidas=C._liberadas("pos"))
        def rpc(nome, a):
            return processar({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": nome, "arguments": a}}, c)["result"]
        self.assertTrue(rpc("cortar", {"projeto": self.pid})["isError"])
        self.assertTrue(rpc("grafico", {"projeto": "outro"})["isError"])
        self.assertIn("compor", C._liberadas("pos"))
        self.assertNotIn("compor", C._liberadas("edicao"))
        chave = C.abrir_chave("codex", self.pid, "pos")
        self.assertEqual(C.contexto_chave(chave)["projeto"], self.pid)
        C.fechar_chave(chave)
        self.assertFalse(C.chave_ok(chave)); self.assertEqual(C.contexto_chave(chave), {})

    def test_curvas_previa_expressao_e_limites(self):
        for curva in ("cinema", "organica"):
            k = [{"t": 0, "x": 0}, {"t": 2, "x": 1, "easing": curva}]
            amostras = [A.valor_em(k, "x", t / 10) for t in range(21)]
            self.assertEqual(amostras[0], 0); self.assertEqual(amostras[-1], 1)
            self.assertEqual(amostras, sorted(amostras))
            self.assertIn("pow", A.curva(k, "x", 0))
        self.assertAlmostEqual(A.valor_em([{ "t": 0, "x": 0 },
            {"t": 1, "x": 1, "easing": "cinema"}], "x", 0.5), 0.875)

    def test_cinema_e_linhas_renderizam_pixels(self):
        # O mesmo ASS é fatiado em exportação. Conferir pixels prova que os
        # novos presets não são apenas campos que a API aceita.
        g = Grafico(tipo="titulo", texto="UMA IDEIA\nPOR VEZ", entrada="linhas",
                    estilo="limpo", out_start=0, out_end=3)
        els = M.elementos(g, 360, 640)
        textos = [e for e in els if any("UMA IDEIA" in str(v) or "POR VEZ" in str(v) for v in e.variantes)]
        self.assertGreaterEqual(len(textos), 2)
        self.assertGreater(max(e.aparece for e in textos), min(e.aparece for e in textos))
        ass = Path(TMP.name) / "motion.ass"
        ass.write_text(M.ass([g], 360, 640, 0, 3), encoding="utf-8-sig")
        # Caminho relativo evita escaping de drive no filtro ASS do Windows.
        r = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
            "color=c=black:s=360x640:r=10:d=3", "-vf", "ass=motion.ass",
            "-f", "rawvideo", "-pix_fmt", "gray", "-"], cwd=TMP.name, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace"))
        frames = np.frombuffer(r.stdout, np.uint8).reshape(-1, 640, 360)
        self.assertGreater(frames[15].sum(), frames[0].sum() + 1000)
        self.assertIn("\\fscx96", ass.read_text(encoding="utf-8-sig"))

    def test_quadros_da_api_aprovam_somente_render_sucesso(self):
        est = self.planejar()
        r = self.tc.post(f"/api/projects/{self.pid}/pos/quadros", json={"tempos": est["tempos_conferir"], "lado": 180})
        self.assertEqual(r.status_code, 200, r.text[:300])
        p = S.load(self.pid)
        self.assertEqual(D.estado(p)["faltam_quadros"], [])
        self.assertTrue(D.revisar(p, "Conferi os três quadros gerados, sem sobrepor a legenda.")["aprovada"])

    def test_codex_comando_conta_e_mcp(self):
        cmd = X.comando("codex.exe", "pos")
        self.assertIn("--ignore-user-config", cmd)
        self.assertIn('forced_login_method="chatgpt"', cmd)
        self.assertIn("features.shell_tool=false", cmd)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        config = dict(x.split("=", 1) for x in cmd if "=" in x)
        self.assertNotIn("cortar", json.loads(config["mcp_servers.sharkcut.enabled_tools"]))

    def test_sucesso_textual_nao_aprova_direcao_sem_conferir(self):
        self.p.plan.direcao = {"ativa": True, "perfil": "editorial"}
        self.p.save_plan()
        with patch.object(X, "editar", return_value={"ok": True, "relatorio": "Ficou ótimo"}):
            resultado = E.executar(self.pid, Contexto(), "codex", "pos")
        self.assertFalse(resultado["ok"])
        self.assertIn("conferência", resultado["erro"])

    def test_cancelar_encerra_processo_e_revoga_chave(self):
        fake = Path(TMP.name) / "codex_lento.py"
        fake.write_text("import sys,time\nsys.stdin.read()\ntime.sleep(30)\n", encoding="utf-8")
        est = {"logado": True, "compativel": True, "caminho": sys.executable, "modelo": ""}
        original = X._abrir
        ctx = Contexto()
        ctx.cancelled = lambda: True
        with patch.object(X, "estado", return_value=est), patch.object(C, "_conferir_porta", return_value=""), \
             patch.object(X, "_abrir", side_effect=lambda cmd, **kw: original([sys.executable, str(fake)], **kw)):
            with self.assertRaises(KeyboardInterrupt):
                X.editar(self.pid, ctx, modo="pos")
        self.assertFalse(C._chaves)

    def test_dois_cliques_na_direcao_nao_trocam_plano_em_curso(self):
        from editor import server
        from editor.jobs import Job
        from unittest.mock import Mock
        job = Job(id="em-curso", project_id=self.pid, kind="diretor", status="rodando")
        fila = Mock()
        fila.list.return_value = [job]
        with patch.object(server, "get_queue", return_value=fila):
            r = server.api_diretor(self.pid, {"provedor": "codex", "modo": "completo"})
        self.assertEqual(r["id"], job.id)
        self.assertEqual(S.load(self.pid).plan.to_dict(), self.p.plan.to_dict())
        fila.submit.assert_not_called()

    def test_codex_eventos_e_falhas_sem_consumir_conta(self):
        fake = Path(TMP.name) / "fake_codex.py"
        fake.write_text('''import json,sys,os
sys.stdin.read()
mode=os.environ.get("FAKE_CODEX", "ok")
print(json.dumps({"type":"thread.started","thread_id":"fake"}),flush=True)
if mode != "vazio":
 print(json.dumps({"type":"item.completed","item":{"id":"call1","type":"mcp_tool_call","server":"sharkcut","tool":"pos_contexto","arguments":{}}}),flush=True)
if mode == "falha":
 print(json.dumps({"type":"turn.failed","error":{"message":"limite atingido"}}),flush=True)
else:
 print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"Conferido."}}),flush=True)
 print(json.dumps({"type":"turn.completed"}),flush=True)
''', encoding="utf-8")
        est = {"logado": True, "compativel": True, "caminho": sys.executable, "modelo": ""}
        original = X._abrir
        with patch.object(X, "estado", return_value=est), patch.object(C, "_conferir_porta", return_value=""), \
             patch.object(X, "_abrir", side_effect=lambda cmd, **kw: original([sys.executable, str(fake)], **kw)):
            for mode, ok in [("ok", True), ("vazio", False), ("falha", False)]:
                with patch.dict(os.environ, {"FAKE_CODEX": mode}):
                    r = X.editar(self.pid, Contexto(), modo="pos")
                self.assertEqual(r["ok"], ok, r)
                self.assertFalse(C._chaves)


if __name__ == "__main__":
    unittest.main(verbosity=2)
