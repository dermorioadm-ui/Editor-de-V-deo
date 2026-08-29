"""Acha e apaga os ambientes Python das copias ANTIGAS do editor.

Cada pasta extraida do ZIP criava um .venv proprio de ~3 GB. Dez
atualizacoes = 30 GB parados no disco, e a instalacao seguinte morria com
"No space left on device" no meio do download.

DUAS TRAVAS, as duas descobertas testando este script numa estrutura igual a
que o usuario tinha (Desktop / Editor de Video / git 1..10 / copia / .venv):

  1. A busca sobe ate 3 pastas e desce ate 3 niveis. Olhar so a pasta atual e
     a vizinha achava UMA das dez copias - as outras nove ficavam ocupando o
     disco e o problema continuava.

  2. Uma pasta so e candidata se ela FOR uma copia do editor: tem que ter
     requirements.txt e a pasta editor/ do lado do .venv. Sem essa prova,
     varrer o Desktop de alguem apagaria o ambiente de qualquer outro projeto
     Python que estivesse por perto - um estrago que ninguem pediu.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SOBE = 3          # quantos niveis acima da pasta atual a busca alcanca
DESCE = 3         # e quantos niveis abaixo de cada um desses


def e_copia_do_editor(pasta: Path) -> bool:
    """Prova de que esta pasta e uma copia deste programa, e nao outra coisa."""
    return ((pasta / "requirements.txt").is_file()
            and (pasta / "editor").is_dir()
            and (pasta / "editor" / "__init__.py").is_file())


def rotulo_de(v: Path) -> str:
    """As duas ultimas pastas do caminho, que e o que identifica a copia."""
    return " / ".join(v.parent.parts[-2:])


def tamanho(p: Path) -> int:
    total = 0
    for raiz, _dirs, arquivos in os.walk(p, onerror=lambda _e: None):
        for a in arquivos:
            try:
                total += (Path(raiz) / a).stat().st_size
            except OSError:
                pass
    return total


def medida(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / (1024 ** 3):.2f} GB"
    return f"{n / (1024 ** 2):.0f} MB"


def procurar(aqui: Path, compartilhado: Path) -> list[Path]:
    achados: list[Path] = []
    vistos: set[Path] = set()
    raizes = [aqui, *list(aqui.parents)[:SOBE]]
    for raiz in raizes:
        for atual, dirs, _arqs in os.walk(raiz, onerror=lambda _e: None):
            atual_p = Path(atual)
            try:
                fundo = len(atual_p.relative_to(raiz).parts)
            except ValueError:
                continue
            if fundo >= DESCE:
                dirs[:] = []
                continue
            # nao entra dentro de ambiente nenhum: e perda de tempo
            dirs[:] = [d for d in dirs if d != ".venv"]
            v = atual_p / ".venv"
            if not v.is_dir():
                continue
            try:
                real = v.resolve()
            except OSError:
                continue
            if real in vistos or real == compartilhado:
                continue
            if not e_copia_do_editor(atual_p):
                continue          # nao e copia do editor: nao e da nossa conta
            vistos.add(real)
            achados.append(v)
    return achados


def main() -> int:
    aqui = Path(__file__).resolve().parent
    try:
        compartilhado = (Path(os.environ.get("LOCALAPPDATA", "/nao-existe"))
                         / "Editor de Video" / "venv").resolve()
    except OSError:
        compartilhado = Path("/nao-existe")

    candidatos = procurar(aqui, compartilhado)
    if not candidatos:
        print("  Nada para limpar: nenhuma copia antiga com ambiente proprio.")
        if compartilhado.exists():
            print(f"  O ambiente compartilhado ({medida(tamanho(compartilhado))})"
                  f" fica onde esta - e ele que faz o editor rodar.")
        return 0

    print(f"  {len(candidatos)} copia(s) antiga(s) do editor com ambiente proprio:\n")
    total = 0
    tamanhos: list[tuple[Path, int]] = []
    for v in candidatos:
        t = tamanho(v)
        tamanhos.append((v, t))
        total += t
        # mostra as duas ultimas pastas: "git 3 / Editor-de-V-deo-..."
        rotulo = rotulo_de(v)
        atual = "  <- esta e a copia que voce esta usando" \
            if v.parent == aqui else ""
        print(f"    {medida(t):>9}   {rotulo}{atual}")
    print(f"\n    {medida(total):>9}   TOTAL a liberar\n")

    if any(v.parent == aqui for v, _ in tamanhos):
        print("  Obs.: a copia ATUAL tambem aparece porque o ambiente dela e")
        print("        antigo (de dentro da pasta). O instalar.bat vai criar o")
        print("        novo, compartilhado, fora daqui.\n")

    try:
        resposta = input("  Apagar esses ambientes? (S/N) ").strip().lower()
    except EOFError:
        resposta = "n"
    if resposta not in ("s", "sim", "y", "yes"):
        print("\n  Nada foi apagado.")
        return 0

    liberado = 0
    for v, t in tamanhos:
        try:
            shutil.rmtree(v)
            liberado += t
            print(f"    apagado: {rotulo_de(v)}")
        except OSError as exc:
            print(f"    NAO deu para apagar {v.parent.name}: {exc}")
            print("      (o editor pode estar aberto usando essa copia)")
    print(f"\n  Liberado: {medida(liberado)}")
    print("\n  Agora rode o instalar.bat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
