"""Libera o espaco que as instalacoes antigas do editor deixaram para tras.

Quem baixa o ZIP a cada atualizacao acumula lixo em QUATRO lugares, e apagar
a pasta so resolve o primeiro:

  1. .venv dentro de cada copia   ~3 GB cada  (some com a pasta)
  2. ambiente compartilhado orfao ~3 GB       (fica em AppData\\Local)
  3. cache do pip                 1 a 5 GB    (fica em AppData\\Local\\pip)
  4. projetos de teste            varia       (ficam em Videos\\Editor de Video)

Nada e apagado sem confirmacao, e cada grupo e confirmado separado. O modelo
de transcricao (~1,5 GB em .cache\\huggingface) NAO entra na lista: apagar
obriga a baixar tudo de novo no proximo EDITAR.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SOBE = 3          # quantos niveis acima da pasta atual a busca alcanca
DESCE = 3         # e quantos niveis abaixo de cada um desses


def e_copia_do_editor(pasta: Path) -> bool:
    """Prova de que esta pasta e uma copia deste programa, e nao outra coisa.

    Sem esta prova, varrer o Desktop de alguem apagaria o ambiente de
    qualquer outro projeto Python que estivesse por perto.
    """
    return ((pasta / "requirements.txt").is_file()
            and (pasta / "editor").is_dir()
            and (pasta / "editor" / "__init__.py").is_file())


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


def rotulo_de(v: Path) -> str:
    return " / ".join(v.parent.parts[-2:])


def venvs_antigos(aqui: Path, compartilhado: Path) -> list[Path]:
    achados: list[Path] = []
    vistos: set[Path] = set()
    for raiz in [aqui, *list(aqui.parents)[:SOBE]]:
        for atual, dirs, _arqs in os.walk(raiz, onerror=lambda _e: None):
            atual_p = Path(atual)
            try:
                if len(atual_p.relative_to(raiz).parts) >= DESCE:
                    dirs[:] = []
                    continue
            except ValueError:
                continue
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
                continue
            vistos.add(real)
            achados.append(v)
    return achados


def confirmar(pergunta: str) -> bool:
    try:
        return input(f"  {pergunta} (S/N) ").strip().lower() in (
            "s", "sim", "y", "yes")
    except EOFError:
        return False


def apagar(alvos: list[tuple[str, Path, int]]) -> int:
    liberado = 0
    for rot, caminho, t in alvos:
        try:
            shutil.rmtree(caminho)
            liberado += t
            print(f"    apagado: {rot}")
        except OSError as exc:
            print(f"    NAO deu para apagar {rot}: {exc}")
            print("      (feche o editor e tente de novo)")
    return liberado


def main() -> int:
    aqui = Path(__file__).resolve().parent
    local = Path(os.environ.get("LOCALAPPDATA", "")) if os.environ.get(
        "LOCALAPPDATA") else Path.home() / ".local"
    try:
        compartilhado = (local / "Editor de Video" / "venv").resolve()
    except OSError:
        compartilhado = Path("/nao-existe")

    liberado = 0

    # ---------------------------------------------------- 1) copias antigas
    velhos = venvs_antigos(aqui, compartilhado)
    if velhos:
        print("\n  [1] AMBIENTES DE COPIAS ANTIGAS DO EDITOR\n")
        alvos = []
        for v in velhos:
            t = tamanho(v)
            atual = "   <- a copia que voce esta usando" if v.parent == aqui else ""
            print(f"    {medida(t):>9}   {rotulo_de(v)}{atual}")
            alvos.append((rotulo_de(v), v, t))
        print(f"    {medida(sum(a[2] for a in alvos)):>9}   total\n")
        if confirmar("Apagar esses ambientes?"):
            liberado += apagar(alvos)
    else:
        print("\n  [1] Nenhuma copia antiga com ambiente proprio. Ok.")

    # ------------------------------------------------- 2) ambiente orfao
    if compartilhado.is_dir() and not e_copia_do_editor(aqui):
        t = tamanho(compartilhado)
        print(f"\n  [2] AMBIENTE COMPARTILHADO ORFAO — {medida(t)}")
        print("      Nenhuma copia do editor foi achada nesta pasta, entao ele")
        print("      pode estar sobrando. Se voce ainda usa o editor, RESPONDA")
        print("      NAO: e ele que faz o programa rodar.\n")
        if confirmar("Apagar o ambiente compartilhado?"):
            liberado += apagar([("ambiente compartilhado", compartilhado, t)])
    elif compartilhado.is_dir():
        print(f"\n  [2] Ambiente compartilhado ({medida(tamanho(compartilhado))})"
              f" fica: e ele que faz o editor rodar.")

    # ----------------------------------------------------- 3) cache do pip
    cache_pip = local / "pip" / "Cache"
    if not cache_pip.is_dir():
        cache_pip = Path.home() / ".cache" / "pip"
    if cache_pip.is_dir():
        t = tamanho(cache_pip)
        if t > 50 * 1024 ** 2:
            print(f"\n  [3] CACHE DO PIP — {medida(t)}")
            print("      Sao os instaladores que ja foram usados. Apagar nao")
            print("      quebra nada; a proxima instalacao so baixa de novo.\n")
            if confirmar("Apagar o cache do pip?"):
                liberado += apagar([("cache do pip", cache_pip, t)])
        else:
            print(f"\n  [3] Cache do pip pequeno ({medida(t)}). Deixa quieto.")

    # ------------------------------------------------ 4) projetos do editor
    dados = None
    casa = Path.home()
    for nome in ("Videos", "Vídeos", "Movies", "Filmes"):
        se = casa / nome / "Editor de Vídeo"
        if se.is_dir():
            dados = se
            break
    if dados is None and (casa / "Editor de Vídeo").is_dir():
        dados = casa / "Editor de Vídeo"
    if dados and (dados / "projects").is_dir():
        projetos = [d for d in (dados / "projects").iterdir() if d.is_dir()]
        t = tamanho(dados / "projects")
        print(f"\n  [4] SEUS PROJETOS — {len(projetos)} projeto(s), {medida(t)}")
        print(f"      em {dados}")
        print("      Aqui moram os cortes, as legendas e as previas dos videos")
        print("      que voce ja editou. Os videos ORIGINAIS nao estao aqui —")
        print("      eles nunca sairam da pasta onde voce gravou.")
        print("      So apague se forem todos testes.\n")
        if confirmar("Apagar TODOS os projetos do editor?"):
            liberado += apagar([("projetos", dados / "projects", t)])

    print(f"\n  ============================================")
    print(f"   Liberado: {medida(liberado)}")
    print(f"  ============================================")
    if liberado:
        print("\n  Agora rode o instalar.bat.")
    # o modelo de transcricao fica, e vale dizer por que
    hf = casa / ".cache" / "huggingface"
    if hf.is_dir():
        print(f"\n  Obs.: o modelo de transcricao ({medida(tamanho(hf))}) NAO foi")
        print("        tocado de proposito. Apagar obriga a baixar tudo de novo")
        print("        no proximo EDITAR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
