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


def _aviso(texto: str) -> None:
    print(texto, file=sys.stderr, flush=True)


def _responder(saida, ident, resultado=None, erro=None) -> None:
    msg = {"jsonrpc": "2.0", "id": ident}
    if erro is not None:
        msg["error"] = erro
    else:
        msg["result"] = resultado
    saida.write(json.dumps(msg, ensure_ascii=False) + "\n")
    saida.flush()


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
            "instructions": (
                "Ferramentas do Sharkcut, o editor de vídeo que roda nesta "
                "máquina. Os arquivos NUNCA saem daqui: as ferramentas recebem "
                "o CAMINHO do arquivo no disco, não o arquivo. Comece por "
                "estado_do_editor se algo parecer errado, e por abrir_video "
                "quando ele disser o nome de um arquivo."
            ),
        }

    if metodo in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return False, None

    if metodo == "ping":
        return True, {}

    if metodo == "tools/list":
        return True, {"tools": catalogo()}

    if metodo == "tools/call":
        params = pedido.get("params") or {}
        nome = params.get("name", "")
        argumentos = params.get("arguments") or {}
        try:
            return True, _texto(chamar(cliente, nome, argumentos))
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
        if isinstance(pedido, list):
            # lote: a especificação permite, e responder a um lote com uma
            # resposta solta quebra clientes que casam id por posição
            respostas = []
            for p in pedido:
                tem, corpo = tratar(p, cliente)
                if tem and p.get("id") is not None:
                    respostas.append({"jsonrpc": "2.0", "id": p["id"],
                                      "result": corpo} if corpo is not None else
                                     {"jsonrpc": "2.0", "id": p["id"],
                                      "error": {"code": -32601,
                                                "message": "método desconhecido"}})
            if respostas:
                saida.write(json.dumps(respostas, ensure_ascii=False) + "\n")
                saida.flush()
            continue
        tem, corpo = tratar(pedido, cliente)
        if not tem:
            continue
        ident = pedido.get("id")
        if corpo is None:
            _responder(saida, ident, erro={
                "code": -32601,
                "message": f"método desconhecido: {pedido.get('method')}"})
        else:
            _responder(saida, ident, resultado=corpo)
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
