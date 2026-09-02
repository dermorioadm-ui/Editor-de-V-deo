"""Libera o espaco que as instalacoes antigas do editor deixaram para tras.

Quem baixa o ZIP a cada atualizacao acumula lixo em QUATRO lugares, e apagar
a pasta so resolve o primeiro:

  1. as copias antigas, com o .venv de ~3 GB dentro de cada uma
     (procuradas no computador inteiro, nao so perto desta pasta)
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
DESCE = 4         # e quantos niveis abaixo de cada raiz de busca

# Onde as copias antigas costumam estar: cada ZIP baixado foi extraido em
# Downloads, na Area de Trabalho, em Documentos, dentro do OneDrive ou na
# raiz de outro disco. A busca antiga olhava so tres niveis em volta DESTA
# pasta - quem tinha a copia velha na Area de Trabalho e a nova em Downloads
# rodava o limpar.bat e ouvia "nenhuma copia antiga". Agora a varredura
# cobre a pasta do usuario inteira e a raiz de cada disco.
PASTAS_DA_CASA = ("Desktop", "Área de Trabalho", "Area de Trabalho",
                  "Downloads", "Documents", "Documentos", "Videos", "Vídeos")
# o que nunca guarda uma copia do editor e so faria a busca demorar
NAO_ENTRA = {"windows", "program files", "program files (x86)", "programdata",
             "$recycle.bin", "system volume information", "appdata",
             "node_modules", ".git", ".cache", "recovery", "perflogs",
             "library", "applications", "proc", "sys", "dev"}


def e_copia_do_editor(pasta: Path) -> bool:
    """Prova de que esta pasta e uma copia deste programa, e nao outra coisa.

    Sem esta prova, varrer o Desktop de alguem apagaria o ambiente de
    qualquer outro projeto Python que estivesse por perto.
    """
    return ((pasta / "requirements.txt").is_file()
            and (pasta / "editor").is_dir()
            and (pasta / "editor" / "__init__.py").is_file())


def raizes_de_busca(aqui: Path) -> list[Path]:
    casa = Path.home()
    raizes = [aqui, *list(aqui.parents)[:SOBE], casa]
    raizes += [casa / n for n in PASTAS_DA_CASA]
    for od in sorted(casa.glob("OneDrive*")):
        raizes.append(od)
        raizes += [od / n for n in PASTAS_DA_CASA]
    if os.name == "nt":
        for letra in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            raizes.append(Path(f"{letra}:\\"))
    unicas: list[Path] = []
    vistas: set[Path] = set()
    for r in raizes:
        try:
            if not r.is_dir():
                continue
            real = r.resolve()
        except OSError:
            continue
        if real in vistas:
            continue
        vistas.add(real)
        unicas.append(real)
    return unicas


def copias_do_editor(aqui: Path) -> list[Path]:
    """Toda pasta do computador que e uma copia do editor, fora ESTA."""
    achadas: list[Path] = []
    vistas: set[Path] = set()
    for raiz in raizes_de_busca(aqui):
        for atual, dirs, _arqs in os.walk(raiz, onerror=lambda _e: None):
            atual_p = Path(atual)
            try:
                fundo = len(atual_p.relative_to(raiz).parts)
            except ValueError:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs
                       if d.lower() not in NAO_ENTRA and not d.startswith(".")
                       and d != "__pycache__"]
            if fundo >= DESCE:
                dirs[:] = []
            if not e_copia_do_editor(atual_p):
                continue
            dirs[:] = []                     # dentro de uma copia nao ha outra
            try:
                real = atual_p.resolve()
            except OSError:
                continue
            if real in vistas or real == aqui:
                continue
            vistas.add(real)
            achadas.append(atual_p)
    return achadas


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


def rotulo_de(pasta: Path) -> str:
    return " / ".join(pasta.parts[-2:])


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
    print("\n  Procurando copias antigas do editor no computador inteiro...")
    copias = copias_do_editor(aqui)
    proprio = aqui / ".venv"
    tem_proprio = proprio.is_dir() and proprio.resolve() != compartilhado
    if copias or tem_proprio:
        print("\n  [1] COPIAS ANTIGAS DO EDITOR\n")
        print("      Elas nao aparecem em \"Aplicativos\" do Windows: cada uma e")
        print("      so uma pasta. O que pesa e o ambiente Python (.venv) que")
        print("      ficou dentro dela.\n")
        ambientes: list[tuple[str, Path, int]] = []
        pastas: list[tuple[str, Path, int]] = []
        for c in copias:
            v = c / ".venv"
            t_v = tamanho(v) if v.is_dir() else 0
            t_c = tamanho(c)
            amb = f"ambiente {medida(t_v)}" if t_v else "sem ambiente"
            print(f"    {medida(t_c):>9}   {rotulo_de(c)}   ({amb})")
            if t_v:
                ambientes.append((rotulo_de(c), v, t_v))
            pastas.append((rotulo_de(c), c, t_c))
        if tem_proprio:
            t_v = tamanho(proprio)
            print(f"    {medida(t_v):>9}   {rotulo_de(aqui)} / .venv   "
                  f"<- ambiente antigo DESTA copia (o app usa o compartilhado)")
            ambientes.append((rotulo_de(aqui) + " / .venv", proprio, t_v))
        print(f"    {medida(sum(a[2] for a in pastas) + (tamanho(proprio) if tem_proprio else 0)):>9}"
              f"   total\n")
        if ambientes and confirmar("Apagar os AMBIENTES dessas copias? (o mais pesado; seguro)"):
            liberado += apagar(ambientes)
            pastas = [(r, c, tamanho(c)) for r, c, _t in pastas]
        if pastas:
            print("\n      As pastas em si sao o programa antigo: codigo e interface.")
            print("      Seus videos e projetos NAO moram nelas. Se voce guardou")
            print("      algum arquivo seu dentro de uma delas, responda NAO e")
            print("      tire antes.\n")
            for r, _c, t in pastas:
                print(f"    {medida(t):>9}   {r}")
            if confirmar("Apagar essas PASTAS inteiras (as versoes antigas)?"):
                liberado += apagar(pastas)
    else:
        print("\n  [1] Nenhuma copia antiga do editor encontrada. Ok.")

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
