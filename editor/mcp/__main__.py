"""O laço do protocolo MCP, por stdin/stdout.

SEM DEPENDÊNCIA NOVA. O SDK oficial do MCP traz um punhado de pacotes, e este
editor vive com cinco dependências no total — o instalador dele já briga por
espaço em disco na máquina do dono (veja o comentário do iniciar.bat sobre os
3 GB por cópia). O protocolo, do lado de um servidor que só expõe ferramentas,
é JSON-RPC 2.0 com uma mensagem JSON por linha: três métodos e um aviso.

REGRA DE OURO DO TRANSPORTE stdio: NADA além de JSON-RPC pode sair no stdout.
Um print() de depuração perdido aqui quebra a conversa inteira do cliente, e o
sintoma do outro lado é "o servidor não responde" — não "alguém imprimiu algo".
Todo recado para humano vai para o stderr.
"""
from __future__ import annotations

import json
import sys
import traceback

from .cliente import Cliente, EditorFora, ErroDoEditor
from .ferramentas import catalogo, chamar

# Versões do protocolo que sei falar. Se o cliente pedir uma destas, respondo
# na mesma; se pedir outra, respondo na mais nova que conheço e deixo que ele
# decida — é o que a especificação manda fazer em vez de recusar a conexão.
VERSOES = ("2025-06-18", "2025-03-26", "2024-11-05")
NOME = "sharkcut"
VERSAO = "1.0.0"


INSTRUCOES = (
    "Ferramentas do Sharkcut, o editor de vídeo que roda nesta máquina. Os "
    "arquivos NUNCA saem daqui: as ferramentas recebem o CAMINHO do arquivo no "
    "disco, não o arquivo. Comece por estado_do_editor se algo parecer errado, "
    "e por abrir_video quando ele disser o nome de um arquivo.\n\n"
    "VOCÊ É O EDITOR. Quando ele pedir para você editar (ou fazer a "
    "pós-edição), o fluxo é:\n"
    "1. editar_sozinho com sem_gemini=true — o corte, a legenda e o zoom saem "
    "pela regra do programa; o Gemini não decide nada.\n"
    "2. A REVISÃO, que é o que o sistema não faz sozinho: leia a transcricao "
    "INTEIRA e corte (cortar) a redundância, a repetição, o take refeito, a "
    "muleta ('né', 'tipo', 'então'), a enrolação — o que tira a dinâmica da "
    "conversa —, sem tocar no gancho, no preço e no CTA; releia com "
    "transcricao restante=true; devolver o que o corte comeu; respiro para o fôlego do corte "
    "de silêncio; ritmo para a velocidade, o zoom e a etapa de cada bloco; "
    "legendas para corrigir palavras mal transcritas e o estilo.\n"
    "3. pos_contexto — o roteiro com os tempos do vídeo FINAL.\n"
    "4. Planeje a pós como um editor de After Effects: um título forte no "
    "gancho; uma TELA de tópico (tipo tela, prefixo 'PARTE 1'...) quando o "
    "assunto muda; LISTA quando ele enumera (cada item entrando na hora em que "
    "é falado, com itens_em); NUMERO quando cita valor ou porcentagem; NOME no "
    "começo; DESTAQUE na palavra que carrega a frase; transições nas emendas "
    "que mudam de assunto (poucas — uma a cada 15–30 s, nunca em toda emenda); "
    "camada desfoque ou escurecer nos momentos de ênfase; parallax para o "
    "efeito 3D; texto ATRÁS da pessoa (camada=atras) em títulos grandes quando "
    "a pessoa está no centro. Menos é mais: um gráfico por ideia, nunca em "
    "cima da legenda, nunca cobrindo o rosto.\n"
    "5. analisar_cena antes de posicionar — ela diz onde a pessoa está e o que "
    "está livre.\n"
    "6. ver_quadros para CONFERIR tudo o que pôs, e corrija com grafico(id=...).\n"
    "7. exportar.\n\n"
    "MARCA E CENAS: leia marca antes da pós (nome exato, cores, logos, regras). "
    "Logo = grafico tipo=logo (de lado e transparente; camada=atras passa atrás "
    "da pessoa). cena tipo=moldura põe o vídeo num cartão de um lado e deixa o "
    "outro livre para explicar; arte_3d acao=objeto põe um objeto 3D pronto "
    "(casa, chave, cadeado…) nas cores da marca quando a fala nomeia a coisa. "
    "Leia gosto antes: o que o dono já apagou não volta. O manual completo é a skill "
    "sharkcut-motion (habilidades/sharkcut-motion/SKILL.md)."
)


def _aviso(texto: str) -> None:
    print(texto, file=sys.stderr, flush=True)


def _texto(t: str, erro: bool = False) -> dict:
    return {"content": [{"type": "text", "text": t}], "isError": erro}


def tratar(pedido: dict, cliente: Cliente) -> tuple[bool, dict | None]:
    """Devolve (tem_resposta, corpo). Aviso (sem id) não tem resposta."""
    metodo = pedido.get("method", "")
    tem_id = "id" in pedido and pedido["id"] is not None

    if metodo == "initialize":
        pedida = (pedido.get("params") or {}).get("protocolVersion")
        versao = pedida if pedida in VERSOES else VERSOES[0]
        return True, {
            "protocolVersion": versao,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": NOME, "version": VERSAO},
            "instructions": (f"Você dirige o projeto {cliente.projeto} no Sharkcut. "
                             "Siga o pedido desta execução. As ferramentas estão limitadas "
                             "à etapa autorizada. Tempos são do vídeo final. Confira o "
                             "resultado com ver_quadros. O programa gera a exportação."
                             if cliente.projeto else INSTRUCOES),
        }

    if metodo in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return False, None

    if metodo == "ping":
        return True, {}

    if metodo == "tools/list":
        return True, {"tools": [t for t in catalogo() if cliente.permitidas is None
                                or t["name"] in cliente.permitidas]}

    if metodo == "tools/call":
        params = pedido.get("params") or {}
        nome = params.get("name", "")
        argumentos = params.get("arguments") or {}
        if cliente.permitidas is not None and nome not in cliente.permitidas:
            return True, _texto("ferramenta não permitida nesta etapa", erro=True)
        if cliente.projeto and argumentos.get("projeto", cliente.projeto) != cliente.projeto:
            return True, _texto("esta execução só pode editar o projeto autorizado", erro=True)
        try:
            r = chamar(cliente, nome, argumentos)
            # ferramenta que devolve IMAGEM (ver_quadros) já monta o conteúdo
            return True, (r if isinstance(r, dict) else _texto(r))
        except EditorFora as exc:
            return True, _texto(str(exc), erro=True)
        except ErroDoEditor as exc:
            return True, _texto(f"o editor recusou: {exc}", erro=True)
        except Exception as exc:  # noqa: BLE001
            # Erro de ferramenta VOLTA COMO TEXTO, não como erro de protocolo:
            # o modelo do outro lado consegue ler e corrigir a chamada, enquanto
            # um erro de JSON-RPC ele só vê como falha opaca.
            _aviso(traceback.format_exc())
            return True, _texto(f"{nome} falhou: {exc}", erro=True)

    if not tem_id:
        return False, None
    return True, None  # método desconhecido: quem chama vira erro


def processar(pedido, cliente: Cliente):
    """Uma mensagem JSON-RPC (ou um lote) → a resposta, ou None se não há o
    que responder (aviso). O mesmo miolo serve o stdio (mcp.bat) e a porta
    HTTP (/mcp), que é por onde o Claude chamado pelo próprio Sharkcut entra."""
    if isinstance(pedido, list):
        # lote: a especificação permite, e responder a um lote com uma
        # resposta solta quebra clientes que casam id por posição
        respostas = []
        for p in pedido:
            if not isinstance(p, dict):
                continue
            tem, corpo = tratar(p, cliente)
            if tem and p.get("id") is not None:
                respostas.append({"jsonrpc": "2.0", "id": p["id"],
                                  "result": corpo} if corpo is not None else
                                 {"jsonrpc": "2.0", "id": p["id"],
                                  "error": {"code": -32601,
                                            "message": "método desconhecido"}})
        return respostas or None
    if not isinstance(pedido, dict):
        return None
    tem, corpo = tratar(pedido, cliente)
    if not tem:
        return None
    msg = {"jsonrpc": "2.0", "id": pedido.get("id")}
    if corpo is None:
        msg["error"] = {"code": -32601,
                        "message": f"método desconhecido: {pedido.get('method')}"}
    else:
        msg["result"] = corpo
    return msg


def servir(entrada=None, saida=None, cliente: Cliente | None = None) -> int:
    entrada = entrada if entrada is not None else sys.stdin
    saida = saida if saida is not None else sys.stdout
    cliente = cliente or Cliente()
    for linha in entrada:
        linha = linha.strip()
        if not linha:
            continue
        try:
            pedido = json.loads(linha)
        except json.JSONDecodeError:
            _aviso(f"linha que não é JSON, ignorada: {linha[:120]}")
            continue
        resposta = processar(pedido, cliente)
        if resposta is not None:
            saida.write(json.dumps(resposta, ensure_ascii=False) + "\n")
            saida.flush()
    return 0


def main() -> int:
    _aviso(f"{NOME} MCP: {len(catalogo())} ferramentas, falando com "
           f"{Cliente().base}")
    try:
        return servir()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
