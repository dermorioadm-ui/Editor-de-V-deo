"""Artes nativas editáveis: modelos são pontos de partida, não renderizadores."""
from __future__ import annotations

from .render.composicao import normalizar, TIPOS, CURVAS, FAIXAS

MODELOS = {"fluxo": "Mecanismo em três etapas", "tipografia": "Tipografia em sequência",
           "orbita": "Elementos em órbita", "grafico": "Traçado e número animado"}


def modelo(nome, duracao=6):
    if nome not in MODELOS:
        raise ValueError("modelo desconhecido")
    elementos = []
    def por(tipo, ident, **dados):
        elementos.append({"tipo": tipo, "id": ident, **dados})
    if nome == "fluxo":
        por("texto", "titulo", texto="COMO FUNCIONA", x=500, y=230, corpo=58)
        for i, texto in enumerate(["ENTRADA", "PROCESSO", "RESULTADO"]):
            x=200+i*300
            if i:
                por("tracado", f"linha{i}", inicio=0.6+i*0.65, pontos=[[x-220,490],[x-75,490]],
                    cor="nenhuma", contorno="marca", espessura=4,
                    marcos=[{"t":0.6+i*0.65,"progresso":0},{"t":1.1+i*0.65,"progresso":1}])
            por("grupo", f"etapa{i}", x=x,y=490,inicio=0.3+i*0.65,
                marcos=[{"t":0.3+i*0.65,"escala":0.7,"opacidade":0},
                        {"t":0.9+i*0.65,"escala":1,"opacidade":1,"curva":"saida"}])
            por("elipse",f"bola{i}",pai=f"etapa{i}",largura=135,altura=135,
                cor="nenhuma",contorno="marca",espessura=3)
            por("texto",f"n{i}",pai=f"etapa{i}",texto=f"0{i+1}",corpo=48)
            por("texto",f"rotulo{i}",pai=f"etapa{i}",texto=texto,y=125,corpo=23)
    elif nome == "tipografia":
        for i,texto in enumerate(["UMA IDEIA.","GANHA FORMA.","MOVE PESSOAS."]):
            por("texto",f"texto{i}",texto=texto,x=500,y=340+i*135,corpo=79,
                cor="marca" if i==1 else "texto",inicio=i*0.75,
                marcos=[{"t":i*0.75,"y":385+i*135,"opacidade":0},
                        {"t":i*0.75+0.7,"y":340+i*135,"opacidade":1,"curva":"saida"}])
        por("tracado","sublinhar",pontos=[[130,535],[870,535]],contorno="marca",espessura=3,
            inicio=1,marcos=[{"t":1,"progresso":0},{"t":2,"progresso":1}])
    elif nome == "orbita":
        por("elipse","anel",x=500,y=490,largura=510,altura=510,cor="nenhuma",contorno="marca",espessura=2,opacidade=0.4)
        por("texto","centro",texto="SUA IDEIA",x=500,y=490,corpo=45)
        por("grupo","orbita",x=500,y=490,marcos=[{"t":0,"rotacao":-25},{"t":6,"rotacao":95,"curva":"linear"}])
        import math
        for i in range(3):
            a=i*math.tau/3
            por("elipse",f"satelite{i}",pai="orbita",x=255*math.cos(a),y=255*math.sin(a),
                largura=26+i*7,altura=26+i*7,cor="marca")
        por("texto","rodape",texto="PARTES CONECTADAS",x=500,y=835,corpo=24,opacidade=0.65)
    else:
        por("texto","titulo",texto="EVOLUÇÃO",x=500,y=200,corpo=55)
        por("tracado","eixo",pontos=[[120,370],[120,700],[880,700]],contorno="texto",espessura=2,opacidade=0.2)
        por("tracado","dados",pontos=[[140,660],[290,620],[410,640],[530,520],[670,440],[860,310]],
            contorno="marca",espessura=7,marcos=[{"t":0.5,"progresso":0},{"t":3.4,"progresso":1}])
        por("numero","numero",x=500,y=840,corpo=90,sufixo="%",marcos=[{"t":0.5,"valor":0},{"t":3.4,"valor":100}])
    # Modelos sempre têm a mesma intenção, mesmo numa duração menor.
    for e in elementos:
        if "inicio" in e: e["inicio"] *= duracao/6
        for m in e.get("marcos",[]): m["t"] *= duracao/6
    return normalizar({"tela":[1000,1000],"elementos":elementos},duracao)


def catalogo():
    from . import blender_local
    return {"vetorial": {"disponivel": True, "local": True, "modelos": MODELOS,
        "tipos": TIPOS, "curvas": CURVAS, "animaveis": list(FAIXAS),
        "limites": {"elementos":48,"duracao":60,"marcos_por_elemento":40,"amostras_por_segundo":30},
        "coordenadas":"prancheta tela=[largura,altura], origem no canto superior esquerdo; x/y dos filhos são locais ao grupo",
        "tempos":"inicio/fim e marcos.t dos elementos em segundos desde o início da composição",
        "cores":"#RRGGBB ou marca/texto/fundo/nenhuma; texto usa a fonte do kit salvo fonte explícita",
        "tracado":"polilinha pontos=[[x,y],...], contorno e espessura; progresso desenha por comprimento; pontos_fim e morph transformam a geometria",
        "grupos":"declare pai antes do filho; transforma e anima os filhos juntos",
        "exemplo":modelo("fluxo")}, "blender":blender_local.estado()}


def por(project, dados):
    from . import pos_edicao
    from .projects import duracao_de_saida
    from .render.composicao import numero
    a=numero(dados.get("inicio"),"inicio",0,duracao_de_saida(project))
    b=numero(dados.get("fim"),"fim",0,duracao_de_saida(project))
    if b-a < 0.3 or b-a > 60:
        raise ValueError("arte deve durar de 0,3 a 60 s dentro da montagem")
    comp=(modelo(dados["modelo"],b-a) if dados.get("modelo")
          else normalizar(dados.get("composicao"),b-a))
    g=pos_edicao.por_grafico(project,{"tipo":"composicao","out_start":a,"out_end":b,
        "texto":str(dados.get("nome") or "Arte vetorial")[:80],"composicao":comp,
        "x":dados.get("x",0.5),"y":dados.get("y",0.5),"tamanho":dados.get("tamanho",1),
        "camada":dados.get("camada","frente"),"estilo":dados.get("estilo","editorial"),
        "origem":dados.get("origem","manual")}, dados.get("id"))
    project.save_plan()
    return {"item":g.to_dict(),"conferir":[a+0.1,(a+b)/2,b-0.1]}
