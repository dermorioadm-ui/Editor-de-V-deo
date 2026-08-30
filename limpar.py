"""Libera o espaco que as instalacoes antigas do editor deixaram para tras.

Quem baixa o ZIP a cada atualizacao acumula lixo em QUATRO lugares, e apagar
a pasta so resolve o primeiro:

  1. .venv dentro de cada copia   ~3 GB cada  (some com a pasta)
  2. ambiente compartilhado orfao ~3 GB       (fica em AppData\\Local)
  3. cache do pip                 1 a 5 GB    (fica em AppData\\Local\\pip)
  4. projetos de teste            varia       (ficam em Videos\\Editor de Video)
  5. modelos de transcricao       ate 10 GB   (.cache\\huggingface\\hub)
  6. cache de download do xet     ate 8 GB    (.cache\\huggingface\\xet)

O grupo 5 foi o que apareceu maior na maquina do usuario: 9,36 GB. O app usa
UM modelo — large-v3 na GPU, turbo na CPU. Todo o resto sao modelos que
alguma instalacao antiga baixou e ninguem mais abre. O modelo EM USO nunca e
oferecido para apagar; os outros sim, com o tamanho de cada um na tela.

Nada e apagado sem confirmacao, e cada grupo e confirmado separado.
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


# O app usa UM modelo por vez (editor/transcribe.py: resolve_model).
# Guardar os dois cobre as duas maquinas; o resto e sobra.
MODELOS_EM_USO = ("large-v3", "turbo")
# so mexemos em modelo de TRANSCRICAO: se o usuario usa outra ferramenta de
# IA que guarda modelo no mesmo cache, ela nao e da nossa conta
MODELOS_NOSSOS = ("whisper",)

# Pastas do cache do HuggingFace que sao PURO cache de download: apagar nao
# perde nada, no maximo faz baixar de novo. O "xet" e o novo backend de
# transferencia (vem junto do hf-xet, dependencia do faster-whisper) e guarda
# os pedacos brutos dos arquivos ALEM do modelo montado — ou seja, o mesmo
# dado duas vezes. Foi o que explicou 7,5 GB que nao apareciam na conta dos
# modelos na maquina do usuario.
CACHE_PURO = {
    "xet": "pedacos de download (o mesmo dado que ja esta no modelo)",
    ".locks": "arquivos de trava de download",
    "tmp": "restos de download interrompido",
}


def modelos_de_transcricao(casa: Path) -> list[tuple[Path, str, int, bool]]:
    """Os modelos baixados, com tamanho e se o app ainda usa cada um."""
    hub = casa / ".cache" / "huggingface" / "hub"
    if not hub.is_dir():
        return []
    saida = []
    for d in sorted(hub.iterdir()):
        if not d.is_dir() or not d.name.startswith("models--"):
            continue
        nome = d.name.removeprefix("models--").replace("--", "/")
        baixo = nome.lower()
        if not any(m in baixo for m in MODELOS_NOSSOS):
            continue                       # modelo de outra ferramenta: passa
        # "distil" e "en" sao variantes que o app NUNCA carrega: sem esta
        # linha, um distil-whisper-large-v3 seria marcado "em uso" só por
        # conter "large-v3" no nome, e ficaria ocupando disco para sempre.
        variante = any(x in baixo for x in ("distil", "-en", ".en"))
        em_uso = (not variante) and any(m in baixo for m in MODELOS_EM_USO)
        saida.append((d, nome, tamanho(d), em_uso))
    return saida


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

    # ------------------------------------------- 5) modelos de transcricao
    modelos = modelos_de_transcricao(casa)
    if modelos:
        sobrando = [m for m in modelos if not m[3]]
        usados = [m for m in modelos if m[3]]
        total = sum(m[2] for m in modelos)
        print(f"\n  [5] MODELOS DE TRANSCRICAO — {medida(total)} no total")
        print("      O app usa UM: large-v3 se voce tiver placa de video,")
        print("      turbo se for processador. Os outros foram baixados por")
        print("      alguma instalacao antiga e ninguem mais abre.\n")
        for _d, nome, t, em_uso in modelos:
            marca = "  <- EM USO, fica" if em_uso else ""
            print(f"    {medida(t):>9}   {nome}{marca}")
        if not sobrando:
            print("\n      Nenhum sobrando: so o que o app usa esta baixado.")
        else:
            print(f"\n    {medida(sum(m[2] for m in sobrando)):>9}   "
                  f"total que da para liberar\n")
            print("      Se um dia voce precisar de um deles de novo, ele baixa")
            print("      sozinho — leva alguns minutos e so na primeira vez.\n")
            if confirmar("Apagar os modelos que o app nao usa?"):
                liberado += apagar([(nome, d, t) for d, nome, t, _u in sobrando])
    else:
        print("\n  [5] Nenhum modelo de transcricao baixado ainda.")

    # ------------------------------- 6) sobras do cache de download
    hf = casa / ".cache" / "huggingface"
    if hf.is_dir():
        contados = sum(m[2] for m in modelos)
        total_hf = tamanho(hf)
        sobra = total_hf - contados
        seguros, outros = [], []
        for d in sorted(hf.iterdir()):
            if not d.is_dir():
                continue
            if d.name == "hub":
                # dentro do hub, o que nao e models-- e sobra
                for sub in sorted(d.iterdir()):
                    if sub.is_dir() and not sub.name.startswith("models--"):
                        t = tamanho(sub)
                        alvo = seguros if sub.name in CACHE_PURO else outros
                        alvo.append((f"hub/{sub.name}", sub, t))
                continue
            t = tamanho(d)
            alvo = seguros if d.name in CACHE_PURO else outros
            alvo.append((d.name, d, t))

        if sobra > 200 * 1024 ** 2 or seguros:
            print(f"\n  [6] SOBRAS DO CACHE DE DOWNLOAD — {medida(sobra)}")
            print("      Isto NAO sao modelos: e o material bruto que o")
            print("      download usou e nunca mais precisa. O mesmo dado ja")
            print("      esta guardado dentro do modelo.\n")
            for rot, _c, t in seguros:
                nome = rot.split("/")[-1]
                print(f"    {medida(t):>9}   {rot}  ({CACHE_PURO.get(nome, '')})")
            for rot, _c, t in outros:
                print(f"    {medida(t):>9}   {rot}  (nao mexo: nao sei o que e)")
            uteis = [x for x in seguros if x[2] > 1024 ** 2]
            if uteis:
                print(f"\n    {medida(sum(x[2] for x in uteis)):>9}   "
                      f"total que da para liberar\n")
                if confirmar("Apagar as sobras de download?"):
                    liberado += apagar(uteis)
            else:
                print("\n      Nada relevante para apagar aqui.")

    print(f"\n  ============================================")
    print(f"   Liberado: {medida(liberado)}")
    print(f"  ============================================")
    if liberado:
        print("\n  Agora rode o instalar.bat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
