#!/bin/bash
cd "$(dirname "$0")" || exit 1

# Para trocar o modelo de transcrição, tire o # da linha abaixo:
# export EDITOR_WHISPER_MODEL=turbo

if [ ! -x ".venv/bin/python" ]; then
  echo
  echo "  O editor ainda não foi instalado nesta pasta."
  echo "  Rode:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  echo
  read -r -p "Aperte Enter para fechar."
  exit 1
fi

.venv/bin/python -m editor || {
  echo
  echo "  O editor fechou com erro. A mensagem acima diz o motivo."
  read -r -p "Aperte Enter para fechar."
}
