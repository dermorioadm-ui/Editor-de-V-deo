// A MESMA CONTA DO RENDER, do lado da prévia.
//
// Isto é uma SEGUNDA implementação da interpolação que editor/render/animacao.py
// faz — e duas implementações da mesma conta divergem sozinhas com o tempo. É
// por isso que existe um teste que gera uma tabela (t, valor) pelos dois lados e
// compara número a número: se um dia alguém mexer só num, a suíte para.
//
// A promessa do produto é "a prévia é ao pixel o que vai baixar". Numa
// sobreposição animada, errar a curva não desalinha um pixel: põe a janela num
// lugar no vídeo e noutro na tela.

export type Curva = 'linear' | 'suave' | 'entra' | 'sai'

export type Marco = {
  t: number
  x?: number
  y?: number
  scale?: number
  opacity?: number
  rotation?: number
  easing?: Curva | string
}

// os mesmos valores de repouso de render/animacao.py:PROPRIEDADES
export const PROPRIEDADES: Record<string, number> = {
  x: 0.5, y: 0.25, scale: 1, opacity: 1, rotation: 0,
}

const CURVAS = ['linear', 'suave', 'entra', 'sai']

type Ponto = { t: number; v: number; c: string }

// Só os marcos que FALAM desta propriedade. Um marco que não traz a chave é de
// outra propriedade (o usuário mexeu só na escala naquele instante) e não deve
// inventar valor para esta — senão mexer na escala arrastaria a posição junto.
function marcos(kfs: Marco[] | undefined | null, chave: string): Ponto[] {
  const fora: Ponto[] = []
  for (const k of kfs ?? []) {
    if (!k || typeof k !== 'object') continue
    const bruto = (k as any)[chave]
    if (bruto === undefined || bruto === null) continue
    const t = Number(k.t)
    const v = Number(bruto)
    if (!Number.isFinite(t) || !Number.isFinite(v)) continue
    const c = String((k as any).easing ?? (k as any).curva ?? 'linear')
    fora.push({ t, v, c: CURVAS.includes(c) ? c : 'linear' })
  }
  fora.sort((a, b) => a.t - b.t)
  return fora
}

export function temAnimacao(kfs: Marco[] | undefined | null, chave: string): boolean {
  const m = marcos(kfs, chave)
  if (m.length < 2) return false
  return m.slice(1).some((p) => Math.abs(p.v - m[0].v) > 1e-9)
}

function progresso(p: number, curva: string): number {
  if (curva === 'suave') return p * p * (3 - 2 * p)
  if (curva === 'entra') return p * p
  if (curva === 'sai') return p * (2 - p)
  return p
}

// Fora do primeiro e do último marco o valor SEGURA — não extrapola. Extrapolar
// manda a janela para fora da tela quando o usuário põe dois marcos no meio do
// vídeo, e ele não pediu nada disso.
export function valorEm(kfs: Marco[] | undefined | null, chave: string,
                        t: number, repouso?: number): number {
  const padrao = repouso === undefined ? (PROPRIEDADES[chave] ?? 0) : repouso
  const m = marcos(kfs, chave)
  if (!m.length) return padrao
  if (t <= m[0].t) return m[0].v
  if (t >= m[m.length - 1].t) return m[m.length - 1].v
  for (let i = 0; i + 1 < m.length; i++) {
    const a = m[i]
    const b = m[i + 1]
    if (a.t <= t && t <= b.t) {
      const vao = b.t - a.t
      const p = vao <= 1e-9 ? 0 : (t - a.t) / vao
      // a curva que vale é a do marco de CHEGADA, como no render
      return a.v + (b.v - a.v) * progresso(p, b.c)
    }
  }
  return m[m.length - 1].v
}

// ---------------------------------------------------------------- máscara
export type Mascara = {
  shape?: string
  cx?: number; cy?: number; rx?: number; ry?: number
  radius?: number; feather?: number
}

/** O recorte da sobreposição em CSS. Devolve o que entra no `style`. */
export function estiloDaMascara(m: Mascara | null | undefined):
    Record<string, string> {
  if (!m || !m.shape) return {}
  const cx = (m.cx ?? 0.5) * 100
  const cy = (m.cy ?? 0.5) * 100
  const rx = (m.rx ?? 0.5) * 100
  const ry = (m.ry ?? 0.5) * 100
  const suave = Math.max(0.001, Math.min(1, m.feather ?? 0.04))
  const dentro = Math.max(0, (1 - suave) * 100)
  if (m.shape === 'elipse') {
    // exato, borda suave inclusive: o gradiente radial é a mesma conta de
    // distância normalizada que o geq faz no alfa
    const g = `radial-gradient(ellipse ${rx}% ${ry}% at ${cx}% ${cy}%, `
      + `#000 ${dentro}%, transparent 100%)`
    return { maskImage: g, WebkitMaskImage: g }
  }
  if (m.shape === 'arredondado') {
    // A FORMA é exata; a borda suave do canto, não. O render amacia o
    // contorno inteiro com a distância ao arco, e o CSS não tem gradiente de
    // retângulo arredondado. O que o usuário posiciona (onde a máscara está e
    // qual o tamanho dela) bate ao pixel; o que difere é a suavidade de alguns
    // pixels na quina. Fica dito aqui em vez de fingir que é igual.
    const r = Math.max(0.01, Math.min(0.99, m.radius ?? 0.18)) * 50
    const c = `inset(${50 - ry}% ${50 - rx}% ${50 - ry}% ${50 - rx}% round ${r}%)`
    return { clipPath: c, WebkitClipPath: c }
  }
  // retângulo: dois gradientes lineares cruzados dão a borda suave nos quatro
  // lados, que é o que o geq faz com a distância de Chebyshev
  const gx = `linear-gradient(to right, transparent ${cx - rx}%, #000 `
    + `${cx - rx * dentro / 100}%, #000 ${cx + rx * dentro / 100}%, `
    + `transparent ${cx + rx}%)`
  const gy = `linear-gradient(to bottom, transparent ${cy - ry}%, #000 `
    + `${cy - ry * dentro / 100}%, #000 ${cy + ry * dentro / 100}%, `
    + `transparent ${cy + ry}%)`
  return {
    maskImage: `${gx}, ${gy}`, WebkitMaskImage: `${gx}, ${gy}`,
    maskComposite: 'intersect', WebkitMaskComposite: 'source-in',
  }
}

// ---------------------------------------------------------------- efeitos
export type Efeito = { kind: string; [k: string]: any }

/**
 * Os efeitos da sobreposição em `filter` do CSS.
 *
 * `fator` leva o sigma do desfoque, que é medido em pixels da FONTE, para os
 * pixels da caixa desenhada na tela — é a mesma régua que a geometria usa.
 *
 * O CHROMA KEY não sai daqui: o CSS não fura cor. Quem tiver chroma vai
 * aparecer verde na prévia e furado no arquivo, e a tela avisa isso com um
 * chip em vez de deixar o usuário descobrir sozinho na exportação.
 */
export function filtroDosEfeitos(efeitos: Efeito[] | null | undefined,
                                 fator = 1): string | undefined {
  const partes: string[] = []
  for (const e of efeitos ?? []) {
    if (!e || typeof e !== 'object') continue
    if (e.kind === 'desfoque') {
      partes.push(`blur(${Math.max(0.1, (Number(e.sigma) || 8) * fator).toFixed(2)}px)`)
    } else if (e.kind === 'cor') {
      const b = Number(e.brightness) || 0
      const s = Number(e.saturation ?? 1)
      const c = Number(e.contrast ?? 1)
      // APROXIMAÇÃO ASSUMIDA: o `eq` do ffmpeg SOMA o brilho ao valor
      // normalizado; o `brightness()` do CSS MULTIPLICA. Para os valores
      // pequenos que um efeito usa as duas curvas quase coincidem, e não há
      // filtro CSS que some. Saturação e contraste são multiplicativos nos
      // dois e batem.
      if (Math.abs(b) > 1e-6) partes.push(`brightness(${(1 + b).toFixed(3)})`)
      if (Math.abs(s - 1) > 1e-6) partes.push(`saturate(${s.toFixed(3)})`)
      if (Math.abs(c - 1) > 1e-6) partes.push(`contrast(${c.toFixed(3)})`)
    }
  }
  return partes.length ? partes.join(' ') : undefined
}

/** O tremor, em pixels do quadro: o mesmo deslocamento que o render soma. */
export function tremorEm(efeitos: Efeito[] | null | undefined, t: number,
                         largura: number, altura: number): { dx: number; dy: number } {
  for (const e of efeitos ?? []) {
    if (!e || e.kind !== 'tremor') continue
    const a = Number(e.amplitude) || 0
    const f = Number(e.frequency) || 6
    if (a <= 1e-6) continue
    return {
      dx: a * largura * Math.sin(2 * Math.PI * f * t),
      dy: a * altura * Math.cos(2 * Math.PI * f * 0.83 * t),
    }
  }
  return { dx: 0, dy: 0 }
}

export function temChroma(efeitos: Efeito[] | null | undefined): boolean {
  return (efeitos ?? []).some((e) => e && e.kind === 'chroma')
}
