"""Proposta automática de velocidade (Parte 4.1).

A regra: acelerar o que é explicação, segurar o que é persuasão.
Errar aqui não é grave — o usuário ajusta. Só precisa ser razoável.
"""
from __future__ import annotations

import re
import unicodedata

from ..config import SpeedParams
from ..models import SECTIONS

_NUM = re.compile(r"\d")
_CURRENCY = re.compile(r"r\$|\breais?\b|\bmil\b|\bcentavos?\b")

KEYWORDS = {
    "garantia": ["garantia", "garantido", "garanto", "risco zero", "devolvo",
                 "devolucao", "reembolso", "sem compromisso", "7 dias", "30 dias"],
    "oferta": ["preco", "preço", "por apenas", "investimento", "parcela",
               "desconto", "promocao", "promoção", "valor", "custa", "assinatura",
               "plano", "r$"],
    "cta": ["clica", "clique", "link", "whatsapp", "chama no", "botao", "botão",
            "agora mesmo", "inscreva", "arrasta", "manda mensagem", "comenta"],
    "prova": ["estudo", "pesquisa", "resultado", "faturou", "faturei", "vendas",
              "cliente", "por cento", "%", "caso", "print", "comprovado",
              "numero", "número", "media", "média"],
    "revelacao": ["segredo", "descobri", "virada", "o que ninguem", "o que ninguém",
                  "a verdade", "sacada", "descoberta", "chave", "revelo"],
    "dor": ["problema", "cansado", "cansada", "dificil", "difícil", "frustra",
            "perde", "perdendo", "sofre", "trava", "nao consegue", "não consegue",
            "erro", "medo", "prejuizo", "prejuízo"],
    "gancho": ["presta atencao", "presta atenção", "olha isso", "se voce",
               "se você", "para tudo", "escuta", "em 60 segundos", "eu vou te"],
    "mecanismo": ["metodo", "método", "passo a passo", "funciona assim",
                  "sistema", "processo", "estrategia", "estratégia", "formula",
                  "fórmula", "tecnica", "técnica", "framework", "o jeito que",
                  "primeiro passo", "etapa"],
    "monetizacao": ["fatura", "faturamento", "lucro", "ganha por", "ganhar por",
                    "receita", "comissao", "comissão", "por mes", "por mês",
                    "renda", "por venda", "ticket", "margem"],
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _hits(norm_text: str, key: str) -> int:
    return sum(1 for kw in KEYWORDS[key] if _norm(kw) in norm_text)


def classify(text: str, position: float, wps: float, duration: float,
             is_last: bool) -> tuple[str, float]:
    """Devolve (seção, confiança 0..1)."""
    n = _norm(text)
    digits = len(_NUM.findall(n))
    has_money = bool(_CURRENCY.search(n))

    scores = {k: 0.0 for k in SECTIONS}
    scores["explicacao"] = 0.8

    # "clica" no meio do vídeo quase sempre é instrução de tutorial, não CTA
    cta_weight = 1.8 if position > 0.6 else 0.35
    scores["garantia"] += 2.4 * _hits(n, "garantia")
    # somava em garantia — bug puro. Com ele, "clica no link agora" virava
    # Garantia com confiança 1,0 e a etapa cta, que tem o enquadramento mais
    # fechado da tabela (1,12), não era emitida uma vez sequer.
    scores["cta"] += cta_weight * _hits(n, "cta")
    scores["oferta"] += 2.2 * _hits(n, "oferta") + (2.0 if has_money else 0.0)
    scores["prova"] += 1.6 * _hits(n, "prova") + min(digits, 6) * 0.35
    scores["revelacao"] += 2.0 * _hits(n, "revelacao")
    scores["dor"] += 1.5 * _hits(n, "dor")
    scores["gancho"] += 1.5 * _hits(n, "gancho")
    scores["mecanismo"] += 1.8 * _hits(n, "mecanismo")
    scores["monetizacao"] += 1.7 * _hits(n, "monetizacao")

    # posição relativa no vídeo
    if position < 0.10:
        scores["gancho"] += 2.2
    elif position < 0.28:
        scores["dor"] += 1.1
    elif position > 0.88 or is_last:
        scores["cta"] += 1.4
        scores["garantia"] += 1.2
        scores["oferta"] += 0.8
    elif position > 0.70:
        scores["oferta"] += 0.7
    if 0.45 < position < 0.75:
        scores["revelacao"] += 0.5

    # densidade: fala corrida costuma ser explicação
    if wps > 3.4:
        scores["explicacao"] += 0.9
    elif wps < 2.1:
        scores["revelacao"] += 0.4
    if duration < 1.6:
        scores["gancho"] += 0.2

    section = max(scores, key=lambda k: scores[k])
    ordered = sorted(scores.values(), reverse=True)
    margin = ordered[0] - (ordered[1] if len(ordered) > 1 else 0.0)
    confidence = max(0.0, min(1.0, margin / 2.5))
    return section, round(confidence, 2)


def suggest_speed(section: str, wps: float, params: SpeedParams) -> float:
    """Velocidade dentro da faixa da seção, puxada pela densidade da fala."""
    lo, hi = SECTIONS.get(section, SECTIONS["explicacao"])["speed"]
    if hi <= lo:
        speed = lo
    else:
        # fala densa (muitas palavras por segundo) aguenta o topo da faixa
        t = max(0.0, min(1.0, (wps - 2.4) / 1.6))
        speed = lo + (hi - lo) * t
    speed = min(speed, params.ceiling)
    speed = max(params.min_speed, min(params.max_speed, speed))
    return round(speed, 2)


def apply_global(speed: float, params: SpeedParams) -> float:
    value = speed * params.global_multiplier
    return round(max(params.min_speed, min(params.max_speed, value)), 2)
