"""python -m editor — sobe tudo em http://localhost:8000"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m editor",
        description="Editor de vídeo local para criativos de anúncio.")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true",
                        help="não abrir o navegador automaticamente")
    parser.add_argument("--check", action="store_true",
                        help="só conferir a instalação e sair")
    parser.add_argument("--test", action="store_true",
                        help="rodar o autoteste (analisa, corta e exporta um "
                             "vídeo de teste) e sair")
    args = parser.parse_args()

    from .config import HOST, PORT, ensure_dirs, ffmpeg_available

    host = args.host or HOST
    port = args.port or PORT
    ensure_dirs()

    ok, detail = ffmpeg_available()
    print("=" * 62)
    print(" Editor de Vídeo — tudo roda na sua máquina")
    print("=" * 62)
    if ok:
        print(f" ffmpeg .......... OK  ({detail})")
    else:
        print(" ffmpeg .......... NÃO ENCONTRADO")
        print(f"   {detail}")
        print("   Veja a seção 'Instalar o ffmpeg' do README.")
    try:
        import faster_whisper  # noqa: F401
        from .config import WHISPER_MODEL
        from .transcribe import detect_device, resolve_model

        dev = detect_device()
        modelo = resolve_model(WHISPER_MODEL, dev.device)
        print(f" transcrição ..... OK  (faster-whisper, modelo {modelo}, "
              f"{dev.device}/{dev.compute_type})")
        if dev.detail:
            print(f"   {dev.detail}")
    except ImportError:
        print(" transcrição ..... faster-whisper NÃO instalado")
        print("   pip install faster-whisper")
    if args.check:
        return 0 if ok else 1
    if args.test:
        if not ok:
            print("\n Sem ffmpeg não dá para rodar o autoteste.")
            return 1
        print("\n Autoteste — isso leva menos de um minuto.\n")
        from .selftest import run as run_selftest

        return 0 if run_selftest() else 1
    if not ok:
        print("\n Sem ffmpeg o editor não consegue trabalhar. Instale e rode de novo.")
        return 1

    import socket

    probe_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe_sock.bind((host, port))
    except OSError:
        print(f"\n A porta {port} já está em uso.")
        print(" O editor provavelmente JÁ está aberto — procure a outra janela")
        print(f" preta, ou abra http://localhost:{port} no navegador.")
        print(" Para rodar uma segunda instância:  set EDITOR_PORT=8001  e rode de novo.")
        return 1
    finally:
        probe_sock.close()

    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    print(f"\n Abrindo {url}")
    print(" Para parar: Ctrl+C nesta janela\n")

    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)),
                         daemon=True).start()

    import uvicorn

    uvicorn.run("editor.server:app", host=host, port=port, log_level="warning",
                ws_ping_interval=20, ws_ping_timeout=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
