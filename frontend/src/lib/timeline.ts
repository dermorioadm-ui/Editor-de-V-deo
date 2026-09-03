import type { Clip, SubtitleCue } from '../types'

/** Posição de saída -> tempo na fonte. */
export function outputToSource(t: number, blocks: Clip[]): { source: string; time: number } | null {
  for (const b of blocks) {
    const s = b.out_start ?? 0
    const e = b.out_end ?? 0
    if (t >= s - 1e-6 && t <= e + 1e-6) {
      const scale = (e - s) / Math.max(b.src_duration, 1e-9)
      return { source: b.source, time: b.src_start + (t - s) / (scale || 1) }
    }
  }
  return null
}

/** Tempo na fonte -> posição de saída (null se o trecho foi removido). */
export function sourceToOutput(t: number, blocks: Clip[], source = 'main'): number | null {
  for (const b of blocks) {
    if (b.source !== source) continue
    if (t >= b.src_start - 1e-6 && t <= b.src_end + 1e-6) {
      const scale = ((b.out_end ?? 0) - (b.out_start ?? 0)) / Math.max(b.src_duration, 1e-9)
      return (b.out_start ?? 0) + (t - b.src_start) * (scale || 1)
    }
  }
  return null
}

/** Tempo na fonte -> posição de saída, caindo no bloco mantido MAIS PERTO
 *  quando o instante está num trecho removido (soltar um item em cima de um
 *  corte não pode simplesmente não fazer nada). */
export function sourceToOutputNearest(t: number, blocks: Clip[], source = 'main'): number | null {
  const direto = sourceToOutput(t, blocks, source)
  if (direto != null) return direto
  let melhor: { d: number; out: number } | null = null
  for (const b of blocks) {
    if (b.source !== source) continue
    const d = t < b.src_start ? b.src_start - t : t - b.src_end
    const out = t < b.src_start ? (b.out_start ?? 0) : (b.out_end ?? 0)
    if (!melhor || d < melhor.d) melhor = { d, out }
  }
  return melhor ? melhor.out : null
}

export function blockAtOutput(t: number, blocks: Clip[]): Clip | null {
  for (const b of blocks) {
    if (t >= (b.out_start ?? 0) - 1e-6 && t < (b.out_end ?? 0) + 1e-6) return b
  }
  return blocks.length ? blocks[blocks.length - 1] : null
}

export function blockAtSource(t: number, blocks: Clip[], source = 'main'): Clip | null {
  for (const b of blocks) {
    if (b.source !== source) continue
    if (t >= b.src_start - 1e-6 && t <= b.src_end + 1e-6) return b
  }
  return null
}

export function cueAt(t: number, cues: SubtitleCue[]): SubtitleCue | null {
  for (const c of cues) if (t >= c.start && t <= c.end) return c
  return null
}

/** Converte as legendas (tempo de saída) para o eixo da fonte, para desenhar. */
export function cuesOnSource(cues: SubtitleCue[], blocks: Clip[]) {
  return cues.map((c) => {
    const a = outputToSource(c.start, blocks)
    const b = outputToSource(Math.max(c.start + 0.01, c.end - 0.01), blocks)
    return { cue: c, start: a?.time ?? null, end: b?.time ?? null }
  }).filter((x) => x.start !== null && x.end !== null) as
    { cue: SubtitleCue; start: number; end: number }[]
}
