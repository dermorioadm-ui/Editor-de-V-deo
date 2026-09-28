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

TIPOS = ("cubo", "esfera", "torus", "cilindro", "plano", "texto", "modelo", "logo3d")
# os objetos prontos (editor/blender_modelos.py) — a lista é repetida aqui
# porque aquele arquivo só pode ser importado DENTRO do Blender
MODELOS = ("casa", "predio", "chave", "cadeado", "escudo", "documento", "celular",
           "calendario", "check", "estrela", "grafico", "mala")
ANIMACOES = ("montar", "surgir", "flutuar", "nenhuma")
PAPEIS_DE_COR = ("marca", "escura", "clara", "base", "texto")


_achado: dict = {}


def _candidatos_windows() -> list[Path]:
    """Onde o blender.exe costuma ficar no Windows — sem varrer o disco: o
    instalador oficial, a Microsoft Store, Steam, Scoop, Chocolatey, winget e
    o ZIP portátil que alguém (ou um agente como o Codex) desempacotou em
    Downloads/Desktop/Documentos/C:\tools."""
    env = os.environ
    home = Path(env.get("USERPROFILE") or Path.home())
    locais = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
    raizes_pf = [Path(env.get(k)) for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")
                 if env.get(k)] or [Path("C:/Program Files")]
    out: list[Path] = []
    for pf in raizes_pf:
        out += sorted((pf / "Blender Foundation").glob("Blender*/blender.exe"), reverse=True)
        out.append(pf / "Blender Foundation" / "blender.exe")
        out.append(pf / "Steam" / "steamapps" / "common" / "Blender" / "blender.exe")
    out += sorted((locais / "Programs" / "Blender Foundation").glob("Blender*/blender.exe"),
                  reverse=True)
    out.append(locais / "Microsoft" / "WindowsApps" / "blender.exe")      # Microsoft Store
    out.append(home / "scoop" / "apps" / "blender" / "current" / "blender.exe")
    out += sorted(Path("C:/ProgramData/chocolatey/lib/blender/tools").glob("blender*/blender.exe"),
                  reverse=True)
    out += sorted((locais / "Microsoft" / "WinGet" / "Packages").glob(
        "BlenderFoundation.Blender*/**/blender.exe"), reverse=True)
    # o ZIP portátil: blender-4.5.9-windows-x64\blender.exe, até dois níveis
    pastas = [home / n for n in ("Downloads", "Desktop", "Documents", "Área de Trabalho",
                                 "Documentos", "tools", "Apps", ".codex", "blender")]
    pastas += [home / "OneDrive" / n for n in ("Desktop", "Documents", "Área de Trabalho",
                                              "Documentos")]
    pastas += [Path("C:/tools"), Path("C:/Blender"), Path("C:/"), Path(__file__).resolve().parents[1]]
    for base in pastas:
        if not base.is_dir():
            continue
        try:
            out += sorted(base.glob("[Bb]lender*/blender.exe"), reverse=True)
            out += sorted(base.glob("*/[Bb]lender*/blender.exe"), reverse=True)
        except OSError:
            continue
    try:                                            # o registro: App Paths e o .blend
        import winreg

        for raiz, chave in ((winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\blender.exe"),
                            (winreg.HKEY_CURRENT_USER,
                             r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\blender.exe"),
                            (winreg.HKEY_CLASSES_ROOT, r"blendfile\shell\open\command")):
            try:
                with winreg.OpenKey(raiz, chave) as k:
                    v = str(winreg.QueryValue(k, None) or "")
                v = v.strip().split('" ')[0].strip('"').split(" %")[0]
                if v.lower().endswith("blender.exe"):
                    out.append(Path(v))
            except OSError:
                continue
    except ImportError:
        pass
    return out


def executavel():
    """O blender.exe: o que ele ESCOLHEU (tela), a variável EDITOR_BLENDER, o
    PATH, e os lugares de instalação comuns. O achado fica guardado."""
    escolhido=os.environ.get("EDITOR_BLENDER", "")
    if escolhido:
        return str(Path(escolhido).resolve()) if Path(escolhido).is_file() else ""
    try:
        from . import db
        salvo=str(db.get_setting("blender_caminho","") or "")
    except Exception:  # noqa: BLE001 — sem banco (teste solto): segue procurando
        salvo=""
    if salvo and Path(salvo).is_file():
        return salvo
    if _achado.get("caminho") and Path(_achado["caminho"]).is_file():
        return _achado["caminho"]
    encontrado=shutil.which("blender")
    if not encontrado:
        candidatos=_candidatos_windows() if os.name=="nt" else []
        candidatos += [Path("/Applications/Blender.app/Contents/MacOS/Blender"),Path("/usr/bin/blender"),
                       Path("/snap/bin/blender")]
        encontrado=next((str(p) for p in candidatos if p.is_file()), "")
    if encontrado:
        _achado["caminho"]=encontrado
    return encontrado


def versao(caminho: str = "") -> str:
    """"Blender 4.5.9" — roda o executável de verdade (e prova que ele abre)."""
    exe=caminho or executavel()
    if not exe:
        return ""
    try:
        r=subprocess.run([exe,"--version"],capture_output=True,text=True,timeout=45,
                         creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    except (OSError,subprocess.TimeoutExpired):
        return ""
    linha=next((ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip().startswith("Blender")),"")
    return linha


def escolher(caminho: str) -> dict:
    """Ele apontou o blender.exe pela janela do sistema: confere e guarda."""
    from . import db
    p=Path(str(caminho or ""))
    if not p.is_file() or p.name.lower() not in ("blender.exe","blender","blender-launcher.exe"):
        raise ValueError("escolha o arquivo blender.exe (na pasta onde o Blender está instalado)")
    if p.name.lower()=="blender-launcher.exe" and (p.parent/"blender.exe").is_file():
        p=p.parent/"blender.exe"      # o launcher abre janela; o blender.exe roda sem tela
    v=versao(str(p))
    if not v:
        raise ValueError("esse blender.exe não abriu — confira se é o Blender 4.x ou 5.x")
    db.set_setting("blender_caminho",str(p))
    _achado.clear()
    return estado()


def estado():
    path=executavel()
    return {"disponivel":bool(path),"executavel":path,"local":True,
            "instalacao":("Instale o Blender (blender.org) ou, se ele já está instalado, aponte o "
                          "blender.exe em Pós → Artes e composição → Apontar o Blender."),
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


def _camadas_do_logo(obj):
    """As camadas de contorno do logo 3D (vindas de logo3d.camadas), conferidas."""
    import re
    cams=obj.get("camadas")
    if not isinstance(cams,list) or not 1<=len(cams)<=3: raise ValueError("logo 3D: de 1 a 3 camadas")
    total=0;out=[]
    for c in cams:
        if not isinstance(c,dict) or not re.fullmatch(r"#[0-9a-fA-F]{6}",str(c.get("cor",""))):
            raise ValueError("logo 3D: camada sem cor #RRGGBB")
        lacos=c.get("lacos")
        if not isinstance(lacos,list) or not 1<=len(lacos)<=400: raise ValueError("logo 3D: laços inválidos")
        novos=[]
        for laco in lacos:
            if not isinstance(laco,list) or len(laco)<3: raise ValueError("logo 3D: laço com menos de 3 pontos")
            novos.append([[numero(p[0],"x",-3,3),numero(p[1],"y",-3,3)] for p in laco
                          if isinstance(p,(list,tuple)) and len(p)==2])
            total+=len(laco)
        out.append({"cor":c["cor"].upper(),"lacos":novos})
    if total>9000: raise ValueError("logo 3D: detalhe demais")
    anim=obj.get("animacao","flutuar")
    if anim not in ("flutuar","nenhuma"): raise ValueError("animacao do logo 3D: flutuar ou nenhuma")
    return {"camadas":out,"animacao":anim}


def cena_de_logo(camadas, duracao=4.5, fps=30, lado=640):
    """O logo 3D sozinho, de frente, flutuando — fundo transparente, sem chão
    (ele flutua no ar, atrás e ao lado da pessoa)."""
    return normalizar({"duracao":duracao,"largura":lado,"altura":lado,"fps":fps,"amostras":10,
                       "estudio":True,
                       "camera":{"posicao":[0.8,-8.0,1.2],"alvo":[0,0,0],"lente":50,"enquadrar":True},
                       "objetos":[{"tipo":"logo3d","camadas":camadas,"animacao":"flutuar"}]})


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
        if obj["tipo"]=="logo3d":
            o.update(_camadas_do_logo(obj))
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


def cena_de_objeto(modelo, duracao=3.0, paleta=None, animacao="montar", lado=720, fps=30):
    """O objeto pronto numa cena de estúdio: luz macia, sombra no chão
    invisível, câmera de três-quartos que enquadra sozinha."""
    return normalizar({"duracao":duracao,"largura":lado,"altura":lado,"fps":fps,"amostras":10,
                       "estudio":True,"sombra":True,
                       "camera":{"posicao":[4.2,-6.0,3.6],"alvo":[0,0,0.85],"lente":50,"enquadrar":True},
                       "objetos":[{"tipo":"modelo","modelo":modelo,"animacao":animacao,
                                   "paleta":paleta or {}}]})


def cena_de_transicao(W, H, duracao=0.8, paleta=None, fps=30):
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


# ------------------------------------------------ o que JÁ FOI GERADO
# "Os elementos já foram gerados": o cache era por PROJETO e pela versão do
# programa e do Blender — projeto novo, ou qualquer atualização do Sharkcut,
# renderizava tudo de novo (minutos por objeto na CPU) e o vídeo saía sem o
# 3D. Agora a chave é O QUE o 3D mostra (o objeto e a cor da marca, o logo, a
# transição no formato): o que já saiu uma vez — em qualquer projeto, em
# qualquer versão — ou que vem pronto com o programa entra NA HORA.
PRONTOS=Path(__file__).with_name("prontos3d")
_memo={}


def _marca(paleta):
    return str((paleta or {}).get("marca") or "#FF385C").upper()


def assinatura(cena):
    """O QUE a cena mostra, sem COMO foi renderizada (duração, fps, amostras,
    tamanho, versão). None para cena livre (essa só o Blender faz)."""
    try:
        cena=normalizar(cena)
    except (ValueError,TypeError):
        return None
    objs=cena.get("objetos") or []
    if cena.get("transicao"):
        return f"transicao:{cena['largura']/cena['altura']:.2f}:{_marca(cena['transicao'].get('paleta'))}"
    if len(objs)==1 and objs[0]["tipo"]=="modelo":
        return f"modelo:{objs[0]['modelo']}:{_marca(objs[0].get('paleta'))}"
    if objs and all(o["tipo"]=="logo3d" for o in objs):
        g=json.dumps([o["camadas"] for o in objs],sort_keys=True)
        return "logo3d:"+hashlib.sha256(g.encode()).hexdigest()[:20]
    return None


def _biblioteca():
    from .config import DATA_DIR
    return DATA_DIR/"prontos-3d"


def _gerados(extra=()):
    """Tudo que o Blender JÁ gerou nesta máquina: a pasta de cada projeto, o
    cache global dos logos e a biblioteca. Lê cada cena.json uma vez só."""
    from .config import DATA_DIR, PROJECTS_DIR
    pastas=[*PROJECTS_DIR.glob("*/artes-3d/*"),*(DATA_DIR/"cache-3d").glob("*"),
            *_biblioteca().glob("*"),*(q for e in extra for q in Path(e).glob("*"))]
    out=[]
    for d in pastas:
        v=d/"arte.mov"
        try:
            st=v.stat()
        except OSError:
            continue
        if st.st_size<=0: continue
        chave=(str(v),st.st_mtime_ns)
        if chave not in _memo:
            try:
                cena=normalizar(json.loads((d/"cena.json").read_text(encoding="utf-8")))
            except (OSError,ValueError,TypeError):
                cena=None
            _memo[chave]=(assinatura(cena) if cena else None,cena)
        a,cena=_memo[chave]
        if a: out.append({"assinatura":a,"arquivo":v,"cena":cena,"quando":st.st_mtime})
    return out


def _do_programa(a):
    """O que vem PRONTO com o Sharkcut para esta assinatura (a mesma peça, em
    qualquer cor: a cor da marca é trocada na hora)."""
    if not a: return None
    try:
        idx=json.loads((PRONTOS/"indice.json").read_text(encoding="utf-8"))
    except (OSError,ValueError):
        return None
    if a.startswith("logo3d:"):
        # logo é o próprio desenho: só o MESMO logo serve, e a cor é a dele
        return next((e for e in idx if e.get("assinatura")==a
                     and (PRONTOS/e.get("arquivo","")).is_file()),None)
    base,_,marca=a.rpartition(":")
    iguais=[e for e in idx if e.get("base")==base and (PRONTOS/e.get("arquivo","")).is_file()]
    if not iguais: return None
    return next((e for e in iguais if e.get("marca","").upper()==marca),iguais[0])


def tem_pronto(cena,extra=()):
    """Existe este 3D sem abrir o Blender? (barato: não converte nada)"""
    a=assinatura(cena)
    if not a: return False
    return any(e["assinatura"]==a for e in _gerados(extra)) or _do_programa(a) is not None


def disponivel(cena,extra=()):
    return tem_pronto(cena,extra) or bool(executavel())


def _hsl(hexcor):
    import colorsys
    r,g,b=(int(hexcor[i:i+2],16)/255 for i in (1,3,5))
    h,l,s=colorsys.rgb_to_hls(r,g,b)
    return h*360,s,l


def _converter(entrada,destino,marca,ctx=None):
    """webm VP9 com alfa (o pronto do programa) -> o MESMO formato que o
    Blender entrega (qtrle argb), já na cor da marca pedida."""
    from .config import FFMPEG
    filtros=[]
    if marca and entrada.get("marca") and entrada["marca"].upper()!=marca.upper():
        # o logo pronto não tem "cor de marca" para trocar: é a cor dele
        h0,s0,_l0=_hsl(entrada["marca"])
        h1,s1,_l1=_hsl(marca)
        filtros.append(f"hue=h={(h1-h0+540)%360-180:.1f}:s={max(0.0,min(4.0,s1/max(s0,1e-3))):.3f}")
    temp=destino.with_name(destino.stem+"-parcial.mov")
    destino.parent.mkdir(parents=True,exist_ok=True)
    # o decodificador libvpx é o que lê o ALFA do VP9 (o nativo o descarta)
    subprocess.run([FFMPEG,"-y","-v","error","-nostdin","-c:v","libvpx-vp9","-i",str(PRONTOS/entrada["arquivo"]),
                    *(["-vf",",".join(filtros)] if filtros else []),
                    "-c:v","qtrle","-pix_fmt","argb","-an",str(temp)],
                   check=True,capture_output=True,timeout=300,
                   creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    temp.replace(destino)
    return destino


def pronto(cena,extra=(),ctx=None):
    """(vídeo, cena do vídeo) do 3D que JÁ EXISTE com esta assinatura — o
    gerado antes (a animação pedida primeiro, depois o mais longo e o mais
    novo) ou o que vem com o programa. None: só o Blender faz."""
    a=assinatura(cena)
    if not a: return None
    cena=normalizar(cena)
    anim=(cena.get("objetos") or [{}])[0].get("animacao")
    iguais=[e for e in _gerados(extra) if e["assinatura"]==a]
    if iguais:
        def nota(e):
            c=e["cena"]
            return ((c.get("objetos") or [{}])[0].get("animacao")==anim,
                    c.get("duracao",0)>=cena["duracao"]-0.05,
                    -abs(c.get("duracao",0)-cena["duracao"]),c.get("fps")==30,e["quando"])
        e=max(iguais,key=nota)
        video=e["arquivo"]
        from .config import PROJECTS_DIR
        try:
            de_projeto=video.resolve().is_relative_to(PROJECTS_DIR.resolve())
        except (OSError,ValueError):
            de_projeto=False
        if de_projeto:
            # veio da pasta de OUTRO projeto (que pode ser apagado): uma cópia
            # vai para a biblioteca, e daqui em diante é ela que serve
            pasta=_biblioteca()/hashlib.sha256(str(video).encode()).hexdigest()[:20]
            if not (pasta/"arte.mov").is_file():
                pasta.mkdir(parents=True,exist_ok=True)
                shutil.copy2(video,pasta/"arte-parcial.mov")
                (pasta/"cena.json").write_text(json.dumps(e["cena"],ensure_ascii=False),encoding="utf-8")
                (pasta/"arte-parcial.mov").replace(pasta/"arte.mov")
            video=pasta/"arte.mov"
        return video,e["cena"]
    ent=_do_programa(a)
    if not ent: return None
    marca="" if a.startswith("logo3d:") else a.rpartition(":")[2]
    pasta=_biblioteca()/"-".join(x for x in (Path(ent['arquivo']).stem,marca.strip('#').lower()) if x)
    video=pasta/"arte.mov"
    feito=normalizar({**cena,**{k:ent[k] for k in ("duracao","largura","altura","fps")}})
    if ent.get("animacao") and feito.get("objetos"):
        feito["objetos"][0]["animacao"]=ent["animacao"]
    if not (video.is_file() and video.stat().st_size>0):
        if ctx: ctx.progress(0.3,"Pegando o 3D pronto da biblioteca","pronto")
        try:
            _converter(ent,video,marca,ctx)
        except (subprocess.SubprocessError,OSError):
            return None
        (pasta/"cena.json").write_text(json.dumps(feito,ensure_ascii=False),encoding="utf-8")
    return video,feito


def _segurar(video,dur,alvo,pasta):
    """O pronto é mais curto que a janela: o último quadro fica parado (a peça
    montada) até o fim — e a saída em fade acontece de verdade."""
    from .config import FFMPEG
    destino=Path(pasta)/f"segura-{hashlib.sha256(f'{video}|{alvo:.3f}'.encode()).hexdigest()[:16]}.mov"
    if destino.is_file() and destino.stat().st_size>0: return destino
    destino.parent.mkdir(parents=True,exist_ok=True)
    temp=destino.with_name(destino.stem+"-parcial.mov")
    subprocess.run([FFMPEG,"-y","-v","error","-nostdin","-i",str(video),
                    "-vf",f"tpad=stop_mode=clone:stop_duration={max(0.0,alvo-dur)+0.1:.3f}",
                    "-t",f"{alvo:.3f}","-c:v","qtrle","-pix_fmt","argb","-an",str(temp)],
                   check=True,capture_output=True,timeout=300,
                   creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    temp.replace(destino)
    return destino


def trabalho(pid,dados,ctx):
    from . import projects as P
    from .models import Overlay
    from .render.renderer import target_size
    p=P.load(pid)
    ini=numero(dados.get("inicio",0),"inicio",0,P.duracao_de_saida(p))
    cena=normalizar(dados.get("cena"))
    # a janela na linha do tempo pode ser MENOR que o render (o logo do gancho
    # é renderizado uma vez, com a duração máxima, e serve a todo vídeo)
    janela=min(cena["duracao"],numero(dados.get("janela",cena["duracao"]),"janela",0.3,10))
    if ini+janela>P.duracao_de_saida(p)+0.001: raise ValueError("3D não cabe na montagem")
    # cache: o da pasta do projeto, ou o GLOBAL (o mesmo logo serve a todo vídeo)
    pasta=Path(dados["cache"]) if dados.get("cache") else p.dir/"artes-3d"
    achado=pronto(cena,[pasta],ctx)
    if achado:
        # JÁ EXISTE (gerado antes ou vindo com o programa): nada de Blender
        video,feito=achado
        if dados.get("cobrir") and abs(feito["duracao"]-cena["duracao"])>0.02:
            # transição de outra duração: o meio dela continua no corte
            ini=max(0.0,ini+(cena["duracao"]-feito["duracao"])/2)
            janela=feito["duracao"]
        elif feito["duracao"]<janela-0.02:
            video=_segurar(video,feito["duracao"],janela,p.dir/"artes-3d")
        cena={**feito,"duracao":max(feito["duracao"],janela)}
    else:
        video,cena=renderizar(cena,pasta,ctx)
    ctx.check()
    p=P.load(pid) # carrega novamente para preservar alterações durante o render
    if ini+janela>P.duracao_de_saida(p)+0.001:
        raise ValueError("a montagem encurtou durante o render; o vídeo 3D está no cache")
    nome=dados.get("nome") or "Arte 3D local"
    if dados.get("substituir"):
        # refeito (o gancho mudou): sai o anterior com o mesmo nome e a mesma origem
        antigos={o.id for o in p.plan.overlays if o.origem==dados.get("origem")
                 and P.nome_da_midia(p,o.media_id)==nome}
        p.plan.overlays=[o for o in p.plan.overlays if o.id not in antigos]
    m=P.add_media(pid,str(video),kind="video",name=nome)
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
    # o TRAJETO: marcos relativos ao começo ({t, x?, y?, escala?, easing?}) —
    # o logo do gancho sai de trás dele, flutua ao lado e volta para trás
    marcos=[]
    for k in dados.get("trajeto") or []:
        mk={"t":round(ini+numero(k.get("t",0),"t",0,janela),4)}
        if "x" in k: mk["x"]=numero(k["x"],"x",-0.5,1.5)
        if "y" in k: mk["y"]=numero(k["y"],"y",-0.5,1.5)
        if "escala" in k: mk["scale"]=round(escala*numero(k["escala"],"escala",0.05,3),5)
        if k.get("easing"): mk["easing"]=str(k["easing"])
        marcos.append(mk)
    camada="atras" if dados.get("camada")=="atras" else ""
    o=Overlay(media_id=m["id"],out_start=ini,out_end=ini+janela,x=x,y=y,
              scale=escala,anim_in="none",anim_out=sai,dur_in=0,dur_out=0.3 if sai=="fade" else 0,
              origem=str(dados.get("origem") or "manual"),keyframes=marcos,camada=camada)
    p.plan.overlays.append(o);p.save_plan()
    return {"ok":True,"overlay":o.to_dict(),"arquivo":str(video),"cena":cena,
            "pronto":bool(achado),
            "conferir":[ini+0.1,ini+janela/2,ini+janela-0.1]}
