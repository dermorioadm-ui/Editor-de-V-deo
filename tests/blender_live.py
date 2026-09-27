"""Teste opcional do Blender real, sem conta IA. python -m tests.blender_live --saida PASTA"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida",required=True)
    op=parser.parse_args();pasta=Path(op.saida).resolve();pasta.mkdir(parents=True,exist_ok=True)
    os.environ["EDITOR_DATA_DIR"]=str(pasta/"dados")
    os.environ["EDITOR_OUTPUT_DIR"]=str(pasta/"saida")
    from fastapi.testclient import TestClient
    import numpy as np
    from editor.server import app
    from editor import projects as P, artes
    from editor.mcp.cliente import Cliente
    from editor.config import FFMPEG
    from tests.mcp import semear
    tc=TestClient(app);pid=semear(Cliente(transporte=tc),pasta)
    p=P.load(pid);p.plan.marca="-";p.plan.export.burn_subtitles=False;p.save_plan()
    cena={"duracao":1,"largura":160,"altura":160,"fps":24,
          "camera":{"posicao":[5,-8,5],"alvo":[0,0,0],"marcos":[{"t":1,"posicao":[8,-5,5]}]},
          "objetos":[{"tipo":"cubo","cor":"#75E0D1","marcos":[{"t":0,"rotacao":[0,0,0]},{"t":1,"rotacao":[0,70,90]}]},
                     {"tipo":"torus","cor":"#F5EEE0","escala":[1.7,1.7,1.7],"rotacao":[15,0,0]}]}
    r=tc.post(f"/api/projects/{pid}/arte-3d",json={"cena":cena,"inicio":1,"origem":"codex"});r.raise_for_status()
    jid=r.json()["id"]
    inicio=time.monotonic();anterior=""
    while True:
        job=next(j for j in tc.get("/api/jobs",params={"project_id":pid}).json() if j["id"]==jid)
        if job["message"]!=anterior:
            print(job["message"],flush=True);anterior=job["message"]
        if job["status"] in ("ok","erro","cancelado"): break
        if time.monotonic()-inicio>300: raise RuntimeError("timeout do teste Blender")
        time.sleep(0.5)
    assert job["status"]=="ok",job
    video=job["result"]["arquivo"]
    r=subprocess.run([FFMPEG,"-v","error","-i",video,"-f","rawvideo","-pix_fmt","rgba","-"],capture_output=True,check=True)
    quadros=np.frombuffer(r.stdout,np.uint8).reshape(-1,160,160,4)
    assert len(quadros)==24
    assert quadros[0,:,:,3].min()==0 and quadros[0,:,:,3].max()==255
    assert np.abs(quadros[0].astype(float)-quadros[-1]).mean()>2
    p=P.load(pid);assert p.plan.overlays[-1].origem=="codex"
    artes.por(p,{"modelo":"tipografia","inicio":3,"fim":6,"origem":"codex"})
    class Ctx:
        def stage(self,*a): print(*a,flush=True)
        def progress(self,*a): pass
        def cancelled(self): return False
    export=P.exportar_final(P.load(pid),Ctx())
    rel={"job":job,"export":export,"quadros":len(quadros),"alfa":"0..255","movimento":True,"segundos":round(time.monotonic()-inicio,2)}
    (pasta/"resultado.json").write_text(json.dumps(rel,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps(rel,ensure_ascii=False,default=str),flush=True)


if __name__=="__main__":main()
