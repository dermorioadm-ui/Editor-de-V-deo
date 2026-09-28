"""Ponte opcional com Blender instalado: cena declarativa, render local e alfa."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time

from .render.composicao import numero

TIPOS = ("cubo", "esfera", "torus", "cilindro", "plano", "texto", "modelo")
# os objetos prontos (editor/blender_modelos.py) — a lista é repetida aqui
# porque aquele arquivo só pode ser importado DENTRO do Blender
MODELOS = ("casa", "predio", "chave", "cadeado", "escudo", "documento", "celular",
           "calendario", "check", "estrela", "grafico", "mala")
ANIMACOES = ("montar", "surgir", "flutuar", "nenhuma")
PAPEIS_DE_COR = ("marca", "escura", "clara", "base", "texto")


def executavel():
    escolhido=os.environ.get("EDITOR_BLENDER", "")
    if escolhido:
        return str(Path(escolhido).resolve()) if Path(escolhido).is_file() else ""
    encontrado=shutil.which("blender")
    if encontrado: return encontrado
    raiz=Path(os.environ.get("ProgramFiles", "C:/Program Files"))/"Blender Foundation"
    candidatos=sorted(raiz.glob("Blender */blender.exe"),reverse=True) if raiz.is_dir() else []
    candidatos += [Path("/Applications/Blender.app/Contents/MacOS/Blender"),Path("/usr/bin/blender")]
    return next((str(p) for p in candidatos if p.is_file()), "")


def estado():
    path=executavel()
    return {"disponivel":bool(path),"executavel":path,"local":True,
            "instalacao":"Instale o Blender ou indique o executável na variável EDITOR_BLENDER antes de iniciar o Sharkcut.",
            "tipos":TIPOS,"saida":"vídeo com transparência, importável como sobreposição",
            "limites":{"objetos":24,"duracao":10,"pixels":2073600},
            "custo_do_render":"sem serviço pago; usa CPU local",
            "esquema":{"duracao":"0.3–10 s","largura":"64–1920 px","altura":"64–1920 px",
                "fps":"24 ou 30","camera":{"posicao":[6,-9,6],"alvo":[0,0,0],"lente":50},
                "objetos":[{"tipo":"cubo","posicao":[0,0,0],"rotacao":[0,0,0],"escala":[1,1,1],
                    "cor":"#75E0D1","metalico":0.3,"rugosidade":0.25,
                    "marcos":[{"t":0,"rotacao":[0,0,0]},{"t":3,"rotacao":[0,0,180]}]}]}}


def _vetor(raw,nome,lo,hi,padrao):
    raw=raw if raw is not None else padrao
    if not isinstance(raw,list) or len(raw)!=3: raise ValueError(f"{nome}: use [x,y,z]")
    return [numero(v,nome,lo,hi) for v in raw]


def _paleta(raw):
    import re
    if raw is None: return {}
    if not isinstance(raw,dict): raise ValueError("paleta: use {marca:#RRGGBB,...}")
    out={}
    for k,v in raw.items():
        if k not in PAPEIS_DE_COR: raise ValueError(f"paleta: papel desconhecido {k}")
        if not isinstance(v,str) or not re.fullmatch(r"#[0-9a-fA-F]{6}",v): raise ValueError("paleta: #RRGGBB")
        out[k]=v.upper()
    return out


def normalizar(raw):
    if not isinstance(raw,dict): raise ValueError("cena 3D precisa ser um objeto")
    dur=numero(raw.get("duracao",3),"duracao",0.3,10)
    w=int(numero(raw.get("largura",720),"largura",64,1920))
    h=int(numero(raw.get("altura",720),"altura",64,1920))
    if w*h>2073600: raise ValueError("render 3D: máximo de 2.073.600 pixels")
    fps=raw.get("fps",24)
    if fps not in (24,30): raise ValueError("fps 3D precisa ser 24 ou 30")
    cam=raw.get("camera",{})
    if not isinstance(cam,dict): raise ValueError("camera precisa ser um objeto")
    camera={"posicao":_vetor(cam.get("posicao"),"câmera",-100,100,[6,-9,6]),
            "alvo":_vetor(cam.get("alvo"),"alvo",-100,100,[0,0,0]),
            "lente":numero(cam.get("lente",50),"lente",15,150)}
    if cam.get("enquadrar"): camera["enquadrar"]=True
    if cam.get("orto") is not None: camera["orto"]=numero(cam.get("orto"),"orto",0.5,100)
    if math.dist(camera["posicao"],camera["alvo"])<0.1: raise ValueError("câmera e alvo precisam estar separados")
    camera["marcos"]=[]
    marcos_cam=cam.get("marcos",[])
    if not isinstance(marcos_cam,list) or len(marcos_cam)>32: raise ValueError("máximo 32 marcos de câmera")
    vistos_cam=set()
    for m in marcos_cam:
        if not isinstance(m,dict) or set(m)-{"t","posicao","alvo","lente"}: raise ValueError("marco de câmera inválido")
        t=numero(m.get("t"),"t",0,dur);v={"t":t}
        for k in ("posicao","alvo","lente"):
            if k in m:
                par=(round(t*fps),k)
                if par in vistos_cam: raise ValueError("marcos de câmera repetidos")
                vistos_cam.add(par)
                v[k]=numero(m[k],"lente",15,150) if k=="lente" else _vetor(m[k],k,-100,100,camera[k])
        if len(v)==1: raise ValueError("marco de câmera sem propriedade")
        camera["marcos"].append(v)
    transicao=None
    if raw.get("transicao") is not None:
        t=raw["transicao"]
        if not isinstance(t,dict): raise ValueError("transicao precisa ser um objeto")
        transicao={"paleta":_paleta(t.get("paleta")),
                   "largura":numero(t.get("largura",5.4),"largura",0.5,100),
                   "altura":numero(t.get("altura",9.6),"altura",0.5,100)}
    itens=raw.get("objetos",[])
    minimo=0 if transicao else 1
    if not isinstance(itens,list) or not minimo<=len(itens)<=24: raise ValueError("de 1 a 24 objetos 3D")
    objs=[]
    import re
    for obj in itens:
        if not isinstance(obj,dict) or obj.get("tipo") not in TIPOS: raise ValueError("tipo 3D desconhecido")
        cor=obj.get("cor","#75E0D1")
        if not isinstance(cor,str) or not re.fullmatch(r"#[0-9a-fA-F]{6}",cor): raise ValueError("cor 3D: #RRGGBB")
        o={"tipo":obj["tipo"],"cor":cor,"texto":str(obj.get("texto",""))[:80],
           "metalico":numero(obj.get("metalico",0.2),"metalico",0,1),
           "rugosidade":numero(obj.get("rugosidade",0.3),"rugosidade",0.05,1)}
        if obj["tipo"]=="modelo":
            if obj.get("modelo") not in MODELOS: raise ValueError(f"modelo 3D: um de {', '.join(MODELOS)}")
            anim=obj.get("animacao","montar")
            if anim not in ANIMACOES: raise ValueError(f"animacao 3D: um de {', '.join(ANIMACOES)}")
            o.update({"modelo":obj["modelo"],"animacao":anim,"paleta":_paleta(obj.get("paleta"))})
        limites={"posicao":(-100,100,[0,0,0]),"rotacao":(-3600,3600,[0,0,0]),"escala":(0.01,20,[1,1,1])}
        for k,(lo,hi,default) in limites.items(): o[k]=_vetor(obj.get(k),k,lo,hi,default)
        marcos=obj.get("marcos",[])
        if not isinstance(marcos,list) or len(marcos)>32: raise ValueError("máximo 32 marcos por objeto")
        o["marcos"]=[]
        vistos=set()
        for m in marcos:
            if not isinstance(m,dict) or set(m)-{"t",*limites}: raise ValueError("marco 3D desconhecido")
            t=numero(m.get("t"),"t",0,dur)
            frame=round(t*fps)
            v={"t":t}
            for k,(lo,hi,default) in limites.items():
                if k in m:
                    if (frame,k) in vistos: raise ValueError("marcos 3D na mesma posição temporal")
                    vistos.add((frame,k));v[k]=_vetor(m[k],k,lo,hi,default)
            if len(v)==1: raise ValueError("marco 3D sem transformação")
            o["marcos"].append(v)
        objs.append(o)
    out={"versao":1,"duracao":dur,"largura":w,"altura":h,"fps":fps,"camera":camera,"objetos":objs}
    if raw.get("estudio"): out["estudio"]=True
    if raw.get("sombra"): out["sombra"]=True
    if raw.get("amostras") is not None: out["amostras"]=int(numero(raw["amostras"],"amostras",4,64))
    if transicao: out["transicao"]=transicao
    return out


def paleta_do_kit(kit):
    """As cores do kit da marca nos papéis que os objetos usam."""
    cores=(kit or {}).get("cores") or {}
    p={"marca":cores.get("marca") or "#FF385C"}
    if cores.get("marca_escura"): p["escura"]=cores["marca_escura"]
    if cores.get("fundo"): p["clara"]=cores["fundo"] if cores["fundo"].upper()!="#FFFFFF" else "#FBF8F4"
    if cores.get("texto"): p["texto"]=cores["texto"]
    return _paleta(p)


def cena_de_objeto(modelo, duracao=3.0, paleta=None, animacao="montar", lado=720, fps=24):
    """O objeto pronto numa cena de estúdio: luz macia, sombra no chão
    invisível, câmera de três-quartos que enquadra sozinha."""
    return normalizar({"duracao":duracao,"largura":lado,"altura":lado,"fps":fps,"amostras":10,
                       "estudio":True,"sombra":True,
                       "camera":{"posicao":[4.2,-6.0,3.6],"alvo":[0,0,0.85],"lente":50,"enquadrar":True},
                       "objetos":[{"tipo":"modelo","modelo":modelo,"animacao":animacao,
                                   "paleta":paleta or {}}]})


def cena_de_transicao(W, H, duracao=0.8, paleta=None, fps=24):
    """Três faixas da marca que tampam a tela no meio do tempo — o corte fica ali."""
    esc=min(1.0,960/max(W,H))
    w,h=max(64,int(W*esc)//2*2),max(64,int(H*esc)//2*2)
    alt=9.6;larg=alt*w/h
    return normalizar({"duracao":duracao,"largura":w,"altura":h,"fps":fps,"amostras":8,"estudio":True,
                       "camera":{"posicao":[0,-30,0],"alvo":[0,0,0],"lente":50,"orto":max(alt,larg)},
                       "transicao":{"paleta":paleta or {},"largura":larg,"altura":alt},"objetos":[]})


def _rodar(args, pasta, ctx, limite, etapa, total=0):
    """Log em disco; cancelamento/timeout encerra o processo e não publica cache."""
    with (pasta/f"{etapa}.log").open("w",encoding="utf-8") as log:
        proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        inicio=time.monotonic()
        ultimo_progresso=inicio
        try:
            while proc.poll() is None:
                if ctx.cancelled(): raise KeyboardInterrupt("render 3D cancelado")
                if time.monotonic()-inicio>limite: raise RuntimeError("render 3D excedeu o limite de tempo")
                if total and time.monotonic()-ultimo_progresso>2:
                    n=min(total,len(list(pasta.glob("quadro_*.png"))))
                    ctx.progress(0.05+0.8*n/total,f"Blender: {n} de {total} quadros","blender")
                    ultimo_progresso=time.monotonic()
                time.sleep(0.15)
            if proc.returncode:
                raise RuntimeError(f"{etapa} falhou; veja {pasta / (etapa+'.log')}")
        finally:
            if proc.poll() is None:
                proc.kill();proc.wait(timeout=10)


def renderizar(raw,pasta,ctx):
    from .config import FFMPEG, FFPROBE
    cena=normalizar(raw)
    exe=executavel()
    if not exe: raise ValueError("Blender não encontrado. "+estado()["instalacao"])
    worker=Path(__file__).with_name("blender_worker.py")
    versao=Path(exe).stat().st_mtime_ns
    modelos=Path(__file__).with_name("blender_modelos.py")
    chave=hashlib.sha256((json.dumps(cena,sort_keys=True)+str(versao)+worker.read_text(encoding="utf-8")
                          +modelos.read_text(encoding="utf-8")).encode()).hexdigest()[:24]
    pasta=Path(pasta)/chave
    pasta.mkdir(parents=True,exist_ok=True)
    video=pasta/"arte.mov"
    if video.is_file() and video.stat().st_size>0: return video,cena
    spec=pasta/"cena.json";spec.write_text(json.dumps(cena,ensure_ascii=False),encoding="utf-8")
    ctx.progress(0.05,"Renderizando objetos, luzes e câmera no Blender local","blender")
    # todos os núcleos menos um (o editor continua respondendo enquanto renderiza)
    nucleos=str(max(2,(os.cpu_count() or 4)-1))
    _rodar([exe,"--background","--factory-startup","--disable-autoexec","--threads",nucleos,
            "--python-exit-code","2","--python",str(worker),"--",str(spec)],pasta,ctx,1800,"blender",
           math.ceil(cena["duracao"]*cena["fps"]))
    ctx.progress(0.88,"Preparando vídeo com transparência","blender")
    temp=pasta/"arte-parcial.mov"
    _rodar([FFMPEG,"-y","-v","error","-nostdin","-framerate",str(cena["fps"]),
            "-start_number","1","-i",str(pasta/"quadro_%04d.png"),"-frames:v",str(math.ceil(cena["duracao"]*cena["fps"])),
            "-c:v","qtrle","-pix_fmt","argb",str(temp)],pasta,ctx,300,"empacotar")
    p=subprocess.run([FFPROBE,"-v","error","-count_frames","-select_streams","v:0","-show_entries",
        "stream=width,height,pix_fmt,nb_read_frames","-of","json",str(temp)],capture_output=True,text=True,timeout=60,
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),check=True)
    info=json.loads(p.stdout).get("streams",[])
    if (not info or info[0].get("pix_fmt")!="argb" or info[0].get("width")!=cena["largura"]
        or info[0].get("height")!=cena["altura"]
        or int(info[0].get("nb_read_frames",0))!=math.ceil(cena["duracao"]*cena["fps"])):
        raise RuntimeError("Blender produziu um vídeo incompleto ou sem transparência")
    ctx.check()
    temp.replace(video)
    # PNGs são temporários gerados exclusivamente nesta pasta de cache.
    for p in pasta.glob("quadro_*.png"): p.unlink()
    return video,cena


def trabalho(pid,dados,ctx):
    from . import projects as P
    from .models import Overlay
    from .render.renderer import target_size
    p=P.load(pid)
    ini=numero(dados.get("inicio",0),"inicio",0,P.duracao_de_saida(p))
    cena=normalizar(dados.get("cena"))
    if ini+cena["duracao"]>P.duracao_de_saida(p)+0.001: raise ValueError("3D não cabe na montagem")
    video,cena=renderizar(cena,p.dir/"artes-3d",ctx)
    ctx.check()
    p=P.load(pid) # carrega novamente para preservar alterações durante o render
    if ini+cena["duracao"]>P.duracao_de_saida(p)+0.001:
        raise ValueError("a montagem encurtou durante o render; o vídeo 3D está no cache")
    m=P.add_media(pid,str(video),kind="video",name=dados.get("nome") or "Arte 3D local")
    # scale do overlay é relativo à altura da fonte. Encaixa toda a imagem.
    W,H=target_size(p.info,p.plan.export)
    fonteW,fonteH=p.info.display_size
    fator=math.sqrt((W/fonteW)*(H/fonteH))
    if dados.get("cobrir"):
        # a transição: tampa a tela inteira (o corte fica escondido no meio dela)
        escala=max(W/cena["largura"],H/cena["altura"])/fator
    else:
        escala=min(W/cena["largura"],H/cena["altura"])/fator
        escala*=numero(dados.get("tamanho",1.0),"tamanho",0.1,1.5)
    x=numero(dados.get("x",0.5),"x",0,1);y=numero(dados.get("y",0.5),"y",0,1)
    sai="fade" if dados.get("some") else "none"
    o=Overlay(media_id=m["id"],out_start=ini,out_end=ini+cena["duracao"],x=x,y=y,
              scale=escala,anim_in="none",anim_out=sai,dur_in=0,dur_out=0.3 if sai=="fade" else 0,
              origem=str(dados.get("origem") or "manual"))
    p.plan.overlays.append(o);p.save_plan()
    return {"ok":True,"overlay":o.to_dict(),"arquivo":str(video),"cena":cena,
            "conferir":[ini+0.1,ini+cena["duracao"]/2,ini+cena["duracao"]-0.1]}
