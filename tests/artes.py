"""Contratos e pixels do motor de arte. python -m unittest tests.artes -v"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TMP=tempfile.TemporaryDirectory(prefix="sharkcut-artes-",ignore_cleanup_errors=True)
os.environ["EDITOR_DATA_DIR"]=str(Path(TMP.name)/"dados")
os.environ["EDITOR_OUTPUT_DIR"]=str(Path(TMP.name)/"saida")

import numpy as np
from fastapi.testclient import TestClient
from editor import artes, blender_local as B, projects as S, claude_editor as C
from editor.models import Grafico,EditPlan,Overlay
from editor.render import composicao as V, motion as M
from editor.server import app
from editor.mcp.cliente import Cliente
from editor.mcp import ferramentas as F
from editor.mcp.__main__ import processar
from editor.config import FFMPEG
from tests.mcp import semear


class ArteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tc=TestClient(app);cls.c=Cliente(transporte=cls.tc,origem="codex")
        cls.pid=semear(cls.c,Path(TMP.name));cls.plano=S.load(cls.pid).plan.to_dict()

    def setUp(self):
        self.p=S.load(self.pid);self.p.plan=EditPlan.from_dict(self.plano)
        self.p.plan.marca="-";self.p.save_plan()

    def test_hierarquia_e_curvas_por_propriedade(self):
        s=V.normalizar({"elementos":[
            {"id":"g","tipo":"grupo","x":500,"y":500,"rotacao":90,"escala":2,"opacidade":0.5},
            {"id":"t","tipo":"texto","pai":"g","x":100,"opacidade":0.8,"texto":"OK",
             "marcos":[{"t":0,"x":100},{"t":2,"x":200,"curva":"linear"}]}]},3)
        _,v=V.estado(s,1)[0]
        self.assertAlmostEqual(v["x"],500);self.assertAlmostEqual(v["y"],800)
        self.assertAlmostEqual(v["opacidade"],0.4)
        s["elementos"][0]["fim"]=0.5
        self.assertEqual(V.estado(s,1),[])

    def test_invalidos_recusados_sem_mutacao(self):
        for bruto in [{"elementos":[]},{"elementos":[{"id":"a","tipo":"texto","x":float("nan")}]},
            {"elementos":[{"id":"a","tipo":"grupo","pai":"a"}]},
            {"elementos":[{"id":"a","tipo":"texto","marcos":[{"t":0,"x":1},{"t":0,"x":2}]}]},
            {"elementos":[{"id":"a","tipo":"tracado","pontos":[[0,0],[1,1]],"pontos_fim":[[0,0],[2,2],[3,3]]}]}]:
            antes=self.p.plan.to_dict()
            with self.assertRaises(ValueError): artes.por(self.p,{"inicio":0,"fim":3,"composicao":bruto})
            self.assertEqual(antes,self.p.plan.to_dict())
        r=self.tc.post(f"/api/projects/{self.pid}/pos/graficos",json={"tipo":"composicao","composicao":{}})
        self.assertEqual(r.status_code,400)

    def test_criar_editar_persistir_e_tirar_por_autor(self):
        r=json.loads(F.chamar(self.c,"arte",{"acao":"criar","projeto":self.pid,"modelo":"fluxo","inicio":0,"fim":4}))
        g=r["item"];self.assertEqual(g["origem"],"codex")
        g["composicao"]["elementos"][0]["texto"]="NOVA IDEIA"
        self.tc.put(f"/api/projects/{self.pid}/pos/graficos/{g['id']}",json={"composicao":g["composicao"]}).raise_for_status()
        p=S.load(self.pid)
        self.assertEqual(p.plan.graficos[0].composicao["elementos"][0]["texto"],"NOVA IDEIA")
        p.plan.overlays.extend([Overlay(media_id="a"),Overlay(media_id="b",origem="codex")]);p.save_plan()
        F.chamar(self.c,"tirar_da_pos",{"projeto":self.pid,"tudo":True})
        p=S.load(self.pid)
        self.assertFalse(p.plan.graficos);self.assertEqual([o.media_id for o in p.plan.overlays],["a"])

    def test_mcp_etapas_e_projeto(self):
        self.assertIn("arte",C._liberadas("pos"));self.assertIn("arte_3d",C._liberadas("pos"))
        self.assertNotIn("arte",C._liberadas("edicao"))
        cli=Cliente(transporte=self.tc,origem="codex",projeto=self.pid,permitidas=C._liberadas("pos"))
        r=processar({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"arte","arguments":{
            "projeto":"outro","acao":"criar","modelo":"fluxo","inicio":0,"fim":3}}},cli)
        self.assertTrue(r["result"].get("isError"));self.assertFalse(S.load(self.pid).plan.graficos)

    def test_modelos_e_cache_estatico(self):
        for nome in artes.MODELOS:
            c=artes.modelo(nome)
            V.normalizar(c,6)
            s=M.ass([Grafico(tipo="composicao",out_end=6,composicao=c)],640,640,0,6)
            self.assertIn("Dialogue:",s)
        c=V.normalizar({"elementos":[{"id":"t","tipo":"texto","texto":"Estático","x":500,"y":500}]},60)
        s=M.ass([Grafico(tipo="composicao",out_end=60,composicao=c)],640,640,0,60)
        self.assertEqual(s.count("Dialogue:"),1)

    def quadro(self,g,t,t0=0,W=240,H=240):
        pasta=Path(TMP.name)
        ass=pasta/"frame.ass";ass.write_text(M.ass([g],W,H,t0,6),encoding="utf-8")
        escaped=str(ass).replace("\\","/").replace(":",r"\:")
        filtro=f"ass=filename='{escaped}'"
        r=subprocess.run([FFMPEG,"-v","error","-nostdin","-f","lavfi","-i",f"color=c=0x081019:s={W}x{H}:r=30:d=6",
            "-vf",filtro,"-ss",str(t),"-frames:v","1","-f","rawvideo","-pix_fmt","rgb24","pipe:1"],capture_output=True,check=True)
        return np.frombuffer(r.stdout,dtype=np.uint8).reshape(H,W,3)

    def test_pixels_movimento_seam_e_formatos(self):
        c=V.normalizar({"elementos":[{"id":"bola","tipo":"elipse","x":200,"y":500,"largura":130,"altura":130,
            "cor":"#FF4433","marcos":[{"t":0,"x":200},{"t":4,"x":800,"curva":"linear"}]}]},6)
        g=Grafico(tipo="composicao",out_end=6,composicao=c)
        a,b=self.quadro(g,0.5),self.quadro(g,3.5)
        def centro(img):
            ys,xs=np.where((img[:,:,0]>160)&(img[:,:,1]<120));return xs.mean(),len(xs)
        ax,an=centro(a);bx,bn=centro(b)
        self.assertGreater(an,200);self.assertGreater(bn,200);self.assertGreater(bx-ax,80)
        inteiro=self.quadro(g,3.3);trecho=self.quadro(g,1.0,t0=2.3)
        self.assertLess(np.abs(inteiro.astype(float)-trecho).mean(),0.5)
        for W,H in [(240,426),(426,240)]:
            self.assertGreater(centro(self.quadro(g,2,W=W,H=H))[1],200)

    def test_tracado_morph_e_escape(self):
        c=V.normalizar({"elementos":[{"id":"p","tipo":"tracado","pontos":[[100,500],[900,500]],
            "pontos_fim":[[500,100],[500,900]],"cor":"nenhuma","contorno":"#75E0D1","espessura":15,
            "marcos":[{"t":0,"progresso":0},{"t":1,"progresso":1},{"t":2,"morph":0},{"t":4,"morph":1}]}]},6)
        g=Grafico(tipo="composicao",out_end=6,composicao=c)
        a,b=self.quadro(g,1.5),self.quadro(g,4.5)
        def ext(img):
            ys,xs=np.where(img[:,:,1]>100);return np.ptp(xs),np.ptp(ys)
        self.assertGreater(ext(a)[0],ext(a)[1]*5)
        self.assertGreater(ext(b)[1],ext(b)[0]*5)
        c=V.normalizar({"elementos":[{"id":"x","tipo":"texto","texto":"{\\pos(0,0)}\nTeste"}]},2)
        s=M.ass([Grafico(tipo="composicao",out_end=2,composicao=c)],240,240,0,2)
        self.assertIn(r"\NTeste",s);self.assertNotIn(r"{\pos(0,0)}",s)

    def test_blender_validacao_e_ausencia_honesta(self):
        cena={"duracao":1,"objetos":[{"tipo":"cubo"}]}
        for v in [float("nan"),-1,40]:
            with self.assertRaises(ValueError): B.normalizar({**cena,"duracao":v})
        with self.assertRaises(ValueError): B.normalizar({**cena,"objetos":[{"tipo":"python","texto":"print('não')"}]})
        with patch.object(B,"executavel",return_value=""):
            r=self.tc.post(f"/api/projects/{self.pid}/arte-3d",json={"inicio":0,"cena":cena})
            self.assertEqual(r.status_code,400);self.assertIn("Blender",r.json()["detail"])
            self.assertFalse(artes.catalogo()["blender"]["disponivel"])

    def test_render_pendente_nao_aprova_e_nao_perde_novo_pedido(self):
        from editor import diretor as D, jobs, server
        fila=SimpleNamespace(list=lambda pid:[SimpleNamespace(id="render-em-curso",kind="arte-3d",status="rodando")])
        with patch.object(jobs,"get_queue",return_value=fila):
            self.assertFalse(D.estado(self.p)["aprovada"])
            with self.assertRaisesRegex(ValueError,"aguarde"):
                D.revisar(self.p,"Conferi a tipografia e as cores da composição.")
        with patch.object(server,"get_queue",return_value=fila),patch.object(B,"executavel",return_value="blender"):
            r=self.tc.post(f"/api/projects/{self.pid}/arte-3d",json={"inicio":0,"cena":{"duracao":1,"objetos":[{"tipo":"cubo"}]}})
            self.assertEqual(r.status_code,409)
            self.assertIn("render-em-curso",r.json()["detail"])

    def test_cancelamento_blender_encerra_processo(self):
        processos=[]
        popen=subprocess.Popen
        def abrir(*a,**k):
            p=popen(*a,**k);processos.append(p);return p
        ctx=SimpleNamespace(cancelled=lambda:True)
        with patch.object(B.subprocess,"Popen",side_effect=abrir):
            with self.assertRaises(KeyboardInterrupt):
                B._rodar([sys.executable,"-c","import time; time.sleep(20)"],Path(TMP.name),ctx,30,"cancelamento")
        self.assertIsNotNone(processos[0].poll())


if __name__=="__main__": unittest.main()
