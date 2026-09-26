import type { Clip, TimelineView, TrechoDoEixo } from '../types'
import { outputToSource, sourceToOutput, sourceToOutputNearest } from './timeline'

/**
 * O EIXO DA LINHA DO TEMPO: as gravações com fala, uma depois da outra.
 *
 * A linha do tempo desenha a gravação inteira — o que ficou e o que saiu em
 * vermelho —, então o eixo dela é o dos ARQUIVOS, não o do vídeo montado.
 * Com uma gravação só, o eixo é o arquivo (tudo como sempre foi). Com três,
 * são os três arquivos em sequência. Antes o eixo era só o primeiro: os
 * blocos e cortes das outras gravações não apareciam, e as legendas delas
 * eram desenhadas por cima da primeira.
 *
 * Três tempos convivem aqui: o da GRAVAÇÃO (source + time), o do EIXO (um
 * número só, de 0 à soma das gravações) e o de SAÍDA (o vídeo montado).
 */
export interface Eixo {
  trechos: TrechoDoEixo[]
  total: number
  multiplo: boolean
  offset: Record<string, number>
}

export function montarEixo(view: TimelineView | null | undefined,
                           duracaoPadrao = 0): Eixo {
  let trechos = (view?.montagem ?? []).filter((t) => t.duracao > 0)
  if (!trechos.length) {
    const d = view?.source_duration || duracaoPadrao || 1
    trechos = [{ source: 'main', nome: '', ordem: 1, offset: 0, duracao: d }]
  }
  const offset: Record<string, number> = {}
  for (const t of trechos) offset[t.source] = t.offset
  const ultimo = trechos[trechos.length - 1]
  return { trechos, total: ultimo.offset + ultimo.duracao,
           multiplo: trechos.length > 1, offset }
}

/** Tempo dentro de uma gravação -> eixo. Nulo para inserto/foto, que não
 *  são gravações com fala e não moram no eixo. */
export function paraEixo(eixo: Eixo, source: string | undefined, t: number): number | null {
  const o = eixo.offset[source || 'main']
  return o == null ? null : o + t
}

/** Eixo -> gravação e tempo dentro dela. */
export function doEixo(eixo: Eixo, x: number): { source: string; time: number; trecho: TrechoDoEixo } {
  let tr = eixo.trechos[0]
  for (const t of eixo.trechos) if (x >= t.offset - 1e-9) tr = t
  return { source: tr.source, trecho: tr,
           time: Math.max(0, Math.min(tr.duracao, x - tr.offset)) }
}

/** Vídeo montado -> eixo. */
export function saidaParaEixo(t: number, blocks: Clip[], eixo: Eixo): number | null {
  const pos = outputToSource(t, blocks)
  return pos ? paraEixo(eixo, pos.source, pos.time) : null
}

/** Eixo -> vídeo montado (nulo quando o ponto caiu num trecho cortado). */
export function eixoParaSaida(x: number, blocks: Clip[], eixo: Eixo): number | null {
  const { source, time } = doEixo(eixo, x)
  return sourceToOutput(time, blocks, source)
}

/** Eixo -> vídeo montado, caindo no bloco mantido mais perto DA MESMA
 *  gravação quando o ponto é trecho cortado. */
export function eixoParaSaidaPerto(x: number, blocks: Clip[], eixo: Eixo): number | null {
  const { source, time } = doEixo(eixo, x)
  return sourceToOutputNearest(time, blocks, source)
}

/** Um ponto de SAÍDA (borda de item de trilho) no eixo, mesmo quando cai
 *  exatamente na emenda de um corte ou passa do fim do vídeo. */
export function saidaParaEixoPerto(t: number, blocks: Clip[], eixo: Eixo): number | null {
  const direto = saidaParaEixo(t, blocks, eixo)
  if (direto != null) return direto
  let melhor: { d: number; x: number } | null = null
  for (const b of blocks) {
    const o = eixo.offset[b.source]
    if (o == null) continue
    const s = b.out_start ?? 0; const e = b.out_end ?? 0
    const d = t < s ? s - t : t > e ? t - e : 0
    const x = o + (t < s ? b.src_start : b.src_end)
    if (!melhor || d < melhor.d) melhor = { d, x }
  }
  return melhor ? melhor.x : null
}

/** As palavras de TODAS as gravações, na ordem da montagem. As do arquivo
 *  principal vêm sem `source` (projeto antigo) e ganham 'main'; as das
 *  outras já vêm com o número global (100000 + posição, 200000 + ...). */
export function palavrasDaMontagem(analysis: any): any[] {
  const principal = (analysis?.words ?? []).map((w: any) =>
    (w.source ? w : { ...w, source: 'main' }))
  const fontes = Object.values(analysis?.fontes ?? {}) as any[]
  fontes.sort((a, b) => (a?.ordem ?? 0) - (b?.ordem ?? 0))
  const extras = fontes.flatMap((f) => (f?.words ?? []).map((w: any) =>
    (w.source ? w : { ...w, source: f.media_id })))
  return [...principal, ...extras]
}
