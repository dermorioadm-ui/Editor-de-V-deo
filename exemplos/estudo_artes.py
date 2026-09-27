"""Mostruário do motor nativo e, opcionalmente, Blender. Sem IA e sem mídia paga."""
import argparse
from pathlib import Path
import subprocess

from editor import artes, blender_local
from editor.config import FFMPEG
from editor.models import Grafico
from editor.render.motion import ass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--saida",required=True);parser.add_argument("--blender",action="store_true")
    op=parser.parse_args();pasta=Path(op.saida).resolve();pasta.mkdir(parents=True,exist_ok=True)
    gs=[]
    titulos=["TIPOGRAFIA EM MOVIMENTO","UMA IDEIA, VÁRIAS PARTES","DADOS QUE GANHAM FORMA","CAMADAS EM MOVIMENTO"]
    for i,nome in enumerate(["tipografia","fluxo","grafico","orbita"]):
        comp=artes.modelo(nome,4)
        # Envolve a composição numa entrada/saída coordenada sem fixar a arte
        # a um tipo de template do motor.
        grupo={"id":"cena","tipo":"grupo","inicio":0,"fim":4,"marcos":[
            {"t":0,"opacidade":0,"y":25},{"t":0.35,"opacidade":1,"y":0,"curva":"saida"},
            {"t":3.7,"opacidade":1},{"t":4,"opacidade":0}]}
        for e in comp["elementos"]:
            if not e["pai"]:e["pai"]="cena"
        comp["elementos"].insert(0,grupo)
        from editor.render.composicao import normalizar
        comp=normalizar(comp,4)
        gs.append(Grafico(tipo="composicao",texto=nome,composicao=comp,out_start=i*4,out_end=(i+1)*4,
                          estilo="editorial",cor="#75E0D1",x=0.5,y=0.48))
        gs.append(Grafico(tipo="texto",texto=f"0{i+1}  /  {titulos[i]}",out_start=i*4,out_end=(i+1)*4,
                          estilo="editorial",entrada="cinema",y=0.17,tamanho=0.58))
    total=19 if op.blender else 16
    video=None
    if op.blender:
        class Ctx:
            def progress(self,*a):print(*a,flush=True)
            def cancelled(self):return False
            def check(self):pass
        video,_=blender_local.renderizar({"duracao":3,"largura":480,"altura":480,"fps":24,
            "camera":{"posicao":[5,-8,5],"alvo":[0,0,0],"marcos":[{"t":3,"posicao":[8,-5,4]}]},
            "objetos":[{"tipo":"cubo","cor":"#75E0D1","escala":[0.65,0.65,0.65],"metalico":0.5,
                         "marcos":[{"t":0,"rotacao":[10,15,0]},{"t":3,"rotacao":[30,40,130]}]},
                        {"tipo":"torus","cor":"#EFE6D2","metalico":0.7,"rugosidade":0.2,
                         "rotacao":[30,0,0],"escala":[1.5,1.5,1.5],
                         "marcos":[{"t":3,"rotacao":[-25,40,90]}]}]},pasta/"blender",Ctx())
        gs.extend([Grafico(tipo="texto",texto="05  /  OBJETOS, LUZ E CÂMERA",out_start=16,out_end=19,
                           estilo="editorial",entrada="cinema",y=0.17,tamanho=0.58),
                   Grafico(tipo="titulo",texto="3D DE VERDADE.",subtexto="Blender integrado · render local",
                           out_start=16,out_end=19,estilo="editorial",entrada="cinema",y=0.75,tamanho=0.8)])
    gs.extend([Grafico(tipo="texto",texto="SHARKCUT  /  ARTES DO MOTOR",out_start=0,out_end=total,
                       estilo="editorial",entrada="cinema",y=0.08,tamanho=0.55),
               Grafico(tipo="texto",texto="Estudo visual · dados ilustrativos · sem áudio",out_start=0,out_end=total,
                       estilo="editorial",entrada="cinema",y=0.91,tamanho=0.45)])
    (pasta/"artes.ass").write_text(ass(gs,720,1280,0,total),encoding="utf-8-sig")
    cmd=[FFMPEG,"-v","error","-y","-f","lavfi","-i",f"color=c=0x081019:s=720x1280:r=30:d={total}"]
    if video:
        cmd += ["-i",str(video),"-filter_complex",
            "[0:v]drawgrid=w=120:h=160:t=1:c=0x324757@0.14[base];"
            "[1:v]scale=640:640,format=rgba,setpts=PTS-STARTPTS+16/TB[obj];"
            "[base][obj]overlay=x=(W-w)/2:y=(H-h)/2-40:enable='gte(t,16)':eof_action=pass,ass=artes.ass[v]","-map","[v]"]
    else:cmd += ["-vf","drawgrid=w=120:h=160:t=1:c=0x324757@0.14,ass=artes.ass"]
    cmd += ["-an","-c:v","libx264","-preset","fast","-crf","18","-pix_fmt","yuv420p","-movflags","+faststart","mostruario-artes.mp4"]
    subprocess.run(cmd,cwd=pasta,check=True)
    for nome,t in [("tipografia",2.5),("fluxo",7),("grafico",11),("orbita",14.5)]+([("blender",17.5)] if video else []):
        subprocess.run([FFMPEG,"-v","error","-y","-ss",str(t),"-i","mostruario-artes.mp4","-frames:v","1",f"{nome}.png"],cwd=pasta,check=True)
    print(pasta/"mostruario-artes.mp4",flush=True)


if __name__=="__main__":main()
