import type { Quadro } from '../types'

/**
 * O FORMATO de saída, espelhado do servidor (editor/render/renderer.py e
 * editor/projects.py): tamanho do quadro derivado, régua da legenda daquele
 * formato e o recorte concêntrico. A prévia usa isto para mostrar o 9:16 (ou
 * 1:1, 16:9) como ele vai sair — antes ela só mostrava a gravação.
 */
export const PROPORCOES: Record<string, number> = { '1:1': 1, '16:9': 16 / 9, '9:16': 9 / 16 }
const ESCADA: Record<string, number[]> = {
  '1:1': [1080, 720], '16:9': [1080, 720, 540], '9:16': [1920, 1280, 960],
}
const ESTICADA_MAXIMA = 1.25

export function rotuloFormato(f: string): string {
  return f === 'fonte' ? 'como gravado' : f
}

export function janelaDerivada(w: number, h: number, prop: number): [number, number] {
  if (prop <= 0) return [w, h]
  if (prop > w / Math.max(h, 1e-9)) return [w, Math.round(w / prop)]
  return [Math.round(h * prop), h]
}

/** O tamanho em que o formato derivado sai (mesma escada do servidor). */
export function tamanhoDerivado(w: number, h: number, aspecto: string): [number, number] {
  const prop = PROPORCOES[aspecto] ?? 0
  if (prop <= 0) return [w, h]
  const [jw] = janelaDerivada(w, h, prop)
  const escada = ESCADA[aspecto] ?? [h]
  let escolhida = escada[escada.length - 1]
  for (const altura of escada) {
    const largura = altura * prop
    if (jw > 0 && largura / jw <= ESTICADA_MAXIMA) { escolhida = altura; break }
  }
  const largura = Math.round(escolhida * prop)
  return [largura - (largura % 2), escolhida - (escolhida % 2)]
}

// (proporção mínima, fonte, margem_v, contorno, chars) — PADROES_DE_LEGENDA
const PADROES: [number, number, number, number, number][] = [
  [1.5, 0.046, 0.08, 0.0044, 42],
  [0.9, 0.04, 0.11, 0.0042, 32],
  [0.0, 0.034, 0.215, 0.0039, 24],
]
export function padraoDeLegenda(w: number, h: number) {
  const prop = w / Math.max(h, 1e-9)
  const linha = PADROES.find(([min]) => prop >= min) ?? PADROES[PADROES.length - 1]
  const [, f, m, c, ch] = linha
  return { fonte: h * f, margem: h * m, contorno: h * c, chars: ch }
}

/** O estilo da legenda no formato derivado: o padrão daquele formato, vezes
 *  a proporção que o usuário escolheu (menor/maior) — igual a regua_da_legenda. */
export function estiloDoFormato(style: any, sw: number, sh: number, aspecto: string): any {
  if (!style || !(aspecto in PROPORCOES)) return style
  const [tw, th] = tamanhoDerivado(sw, sh, aspecto)
  if (Math.abs(sw / sh - tw / th) <= 0.01) return style
  const base = padraoDeLegenda(sw, sh)
  const fator = base.fonte > 0 ? (style.fontsize ?? base.fonte) / base.fonte : 1
  const p = padraoDeLegenda(tw, th)
  return {
    ...style,
    fontsize: Math.max(8, Math.round(p.fonte * fator)),
    margin_v: Math.max(0, Math.round(p.margem)),
    margin_l: Math.max(0, Math.round(tw * 0.06)),
    margin_r: Math.max(0, Math.round(tw * 0.06)),
    outline: Math.max(1, Math.round(p.contorno * 100) / 100),
    shadow: Math.max(0, Math.round(p.contorno * 25) / 100),
    max_chars_per_line: p.chars,
    _quadro: [tw, th],
  }
}

const par = (v: number) => { const n = Math.floor(v); return n - (n % 2) }
const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v))

/** Recorte CONCÊNTRICO no rosto (edit/zoom.py: recorte). Devolve [x, y, w, h]. */
export function recorte(zoom: number, largura: number, altura: number,
                        cx: number, cy: number, proporcao = 0): [number, number, number, number] {
  const z = Math.max(1, zoom || 1)
  let bw = largura, bh = altura
  if (proporcao > 0) {
    if (proporcao > bw / Math.max(bh, 1e-9)) bh = bw / proporcao
    else bw = bh * proporcao
  }
  const w = par(bw / z), h = par(bh / z)
  const x = par(clamp(cx * largura - w / 2, 0, largura - w))
  const y = par(clamp(cy * altura - h / 2, 0, altura - h))
  return [x, y, w, h]
}

/** Quebra gulosa para a régua de outro formato (linebreak.requebrar). */
export function quebrar(texto: string, maxChars: number, maxLines: number): string {
  const palavras = String(texto ?? '').split(/\s+/).filter(Boolean)
  if (!palavras.length) return String(texto ?? '')
  if (String(texto).split('\n').every((l) => l.length <= maxChars)) return String(texto)
  const linhas: string[] = []
  let cur = ''
  for (const p of palavras) {
    const cand = cur ? `${cur} ${p}` : p
    if (cand.length <= maxChars || !cur) cur = cand
    else { linhas.push(cur); cur = p }
  }
  if (cur) linhas.push(cur)
  void maxLines
  return linhas.join('\n')
}

export const QUADRO_PADRAO: Quadro = { modo: 'encaixe', escala: 1, x: 0.5, y: 0.5, fundo: 'preto' }
