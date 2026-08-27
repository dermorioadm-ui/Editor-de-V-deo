import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Clip, Envelope, TimelineView } from '../types'
import { SECTIONS } from '../types'
import { clamp, timecode } from '../lib/format'
import { cuesOnSource, sourceToOutput } from '../lib/timeline'
import { getPlayhead, setPlayhead, setState, subscribePlayhead, useStore }
  from '../state/store'

interface Props {
  view: TimelineView
  envelope: Envelope | null
  sourceDuration: number
  onDeleteSelection: () => void
  onDeleteClip: (clipId: string) => void
  onRestore: (start: number, end: number) => void
  onToggleTake: (id: string, restored: boolean) => void
  onToggleClap: (id: string, enabled: boolean) => void
  onSubtitleEdge: (cueId: string, side: 'start' | 'end', outTime: number) => void
}

// Faixas, de cima para baixo. Marcas (palma, alerta) saíram de cima da onda:
// riscos vermelhos em cima da onda vermelha do trecho removido era o que
// deixava a trilha confusa.
const ROW = { ruler: 18, marks: 15, wave: 78, sections: 16, blocks: 28, subs: 24 }
const PAD_TOP = 12

/** Cor por velocidade: dá para ver onde está acelerado sem ler número nenhum. */
function speedColor(speed: number): string {
  if (speed <= 1.005) return '#3b82f6'
  if (speed <= 1.12) return '#22c55e'
  if (speed <= 1.25) return '#eab308'
  return '#f97316'
}

export default function Timeline(props: Props) {
  const { view, envelope, sourceDuration } = props
  const base = useRef<HTMLCanvasElement>(null)     // onda, blocos, legendas
  const over = useRef<HTMLCanvasElement>(null)     // playhead, seleção, cursor
  const wrap = useRef<HTMLDivElement>(null)
  const [span, setSpan] = useState(sourceDuration || 1)
  const [start, setStart] = useState(0)
  const [size, setSize] = useState({ w: 800, h: 220 })
  const [hover, setHover] = useState<{ x: number; t: number } | null>(null)
  const drag = useRef<{ mode: string; t0: number; x0: number; s0: number
                        cueId?: string } | null>(null)
  const [subDrag, setSubDrag] = useState<
    { id: string; side: 'start' | 'end'; t: number } | null>(null)
  const selection = useStore((s) => s.selection)
  const selectedClip = useStore((s) => s.selectedClip)

  const total = sourceDuration || envelope?.duration || 1
  const height = PAD_TOP + ROW.ruler + ROW.marks + ROW.wave + ROW.sections +
    ROW.blocks + ROW.subs + 8

  const yRuler = PAD_TOP
  const yMarks = yRuler + ROW.ruler
  const yWave = yMarks + ROW.marks
  const ySections = yWave + ROW.wave
  const yBlocks = ySections + ROW.sections
  const ySubs = yBlocks + ROW.blocks

  useEffect(() => { setSpan(total); setStart(0) }, [total])

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: height }))
    ro.observe(el)
    setSize({ w: el.clientWidth, h: height })
    return () => ro.disconnect()
  }, [height])

  const toX = useCallback((t: number) => (t - start) / span * size.w, [start, span, size.w])
  const toT = useCallback((x: number) => start + x / size.w * span, [start, span, size.w])

  const subsOnSource = useMemo(
    () => cuesOnSource(view.subtitles, view.blocks), [view.subtitles, view.blocks])

  // Blocos vizinhos da mesma seção viram UMA faixa com o nome escrito. Com 45
  // blocos num vídeo de 12 minutos, 45 retângulos iguais não dizem nada; 4
  // faixas nomeadas dizem onde está o gancho e onde está a oferta.
  const bands = useMemo(() => {
    const out: { section: string; start: number; end: number; blocks: number }[] = []
    for (const b of view.blocks) {
      if (b.source !== 'main') continue
      const last = out[out.length - 1]
      if (last && last.section === b.section && b.src_start - last.end < 0.35) {
        last.end = b.src_end
        last.blocks += 1
      } else {
        out.push({ section: b.section, start: b.src_start, end: b.src_end, blocks: 1 })
      }
    }
    return out
  }, [view.blocks])

  // Pico por coluna de pixel. O código antigo pegava UM ponto de envelope a
  // cada pixel: com 12 min na tela isso jogava fora 149 de cada 150 amostras
  // e a onda mostrava um serrilhado que não existe no áudio.
  // Pico E vale por coluna de pixel. O código antigo pegava UM ponto de
  // envelope a cada pixel: com 12 min na tela isso jogava fora 149 de cada
  // 150 amostras. Só o pico também não serve — vira um bloco azul maciço.
  // O vale por dentro é o que mostra onde tem respiro entre as palavras.
  const wavePeaks = useMemo(() => {
    const pts = envelope?.points
    if (!pts?.length || size.w < 2) return null
    const n = pts.length
    const dur = envelope!.duration || 1
    const floor = envelope!.noise_floor
    const hi = new Float32Array(size.w)
    const lo = new Float32Array(size.w)
    for (let x = 0; x < size.w; x++) {
      const t0 = start + x / size.w * span
      const t1 = start + (x + 1) / size.w * span
      let i0 = Math.floor(t0 / dur * (n - 1))
      let i1 = Math.ceil(t1 / dur * (n - 1))
      i0 = clamp(i0, 0, n - 1); i1 = clamp(i1, i0 + 1, n)
      let mx = -200; let mn = 200
      for (let i = i0; i < i1; i++) {
        if (pts[i] > mx) mx = pts[i]
        if (pts[i] < mn) mn = pts[i]
      }
      hi[x] = mx > -199 ? mx : floor
      lo[x] = mn < 199 ? mn : floor
    }
    return { hi, lo }
  }, [envelope, start, span, size.w])

  // ------------------------------------------------- camada estática
  useEffect(() => {
    const cv = base.current
    if (!cv) return
    const dpr = window.devicePixelRatio || 1
    cv.width = size.w * dpr; cv.height = height * dpr
    cv.style.width = `${size.w}px`; cv.style.height = `${height}px`
    const g = cv.getContext('2d')!
    g.setTransform(dpr, 0, 0, dpr, 0, 0)

    g.fillStyle = '#0f1218'
    g.fillRect(0, 0, size.w, height)

    // régua
    const step = niceStep(span, size.w)
    g.font = '10px ui-monospace, monospace'
    g.lineWidth = 1
    for (let t = Math.ceil(start / step) * step; t < start + span; t += step) {
      const x = Math.round(toX(t)) + 0.5
      g.strokeStyle = '#1a2030'
      g.beginPath(); g.moveTo(x, yMarks); g.lineTo(x, ySubs + ROW.subs); g.stroke()
      g.fillStyle = '#4b5563'
      g.fillText(timecode(t), x + 3, yRuler + 11)
    }

    // onda
    if (wavePeaks) {
      const floor = envelope!.noise_floor
      const range = Math.max(6, 0 - floor)
      const mid = yWave + ROW.wave / 2
      const half = ROW.wave / 2 - 3
      const amp = (db: number) => clamp((db - floor) / range, 0, 1) * half
      // Quantas amostras de envelope cabem numa coluna de pixel. Longe, o
      // desenho é "envelope + corpo"; de perto, uma coluna é uma amostra só
      // e o pico já É a onda — desenhar o corpo por cima chapava tudo.
      const perColumn = (span / (envelope!.hop || 0.01)) / Math.max(1, size.w)
      g.fillStyle = perColumn >= 2 ? '#1d4067' : '#4d94e0'
      g.beginPath()
      for (let x = 0; x < size.w; x++) {
        const a = amp(wavePeaks.hi[x])
        g.rect(x, mid - a, 1, Math.max(1, a * 2))
      }
      g.fill()
      if (perColumn >= 2) {
        g.fillStyle = '#4d94e0'
        g.beginPath()
        for (let x = 0; x < size.w; x++) {
          const a = amp(wavePeaks.lo[x])
          if (a < 0.5) continue
          g.rect(x, mid - a, 1, a * 2)
        }
        g.fill()
      }
      const sil = clamp((envelope!.silence_threshold - floor) / range, 0, 1) * half
      g.strokeStyle = '#2b3648'
      g.setLineDash([3, 3])
      g.beginPath(); g.moveTo(0, mid - sil); g.lineTo(size.w, mid - sil)
      g.moveTo(0, mid + sil); g.lineTo(size.w, mid + sil); g.stroke()
      g.setLineDash([])
    }

    // o que SAI do vídeo: um só tratamento visual, hachurado, cobrindo a onda
    const drawGone = (x0: number, x1: number, tint: string, line: string,
                      label: string) => {
      const w = x1 - x0
      if (w <= 0) return
      g.save()
      g.beginPath(); g.rect(x0, yWave, w, ROW.wave); g.clip()
      g.fillStyle = tint
      g.fillRect(x0, yWave, w, ROW.wave)
      g.strokeStyle = line
      g.lineWidth = 1
      for (let x = x0 - ROW.wave; x < x1; x += 7) {
        g.beginPath(); g.moveTo(x, yWave + ROW.wave); g.lineTo(x + ROW.wave, yWave)
        g.stroke()
      }
      g.restore()
      g.strokeStyle = line
      g.strokeRect(x0 + 0.5, yWave + 0.5, w - 1, ROW.wave - 1)
      if (w > 74 && label) {
        g.fillStyle = line
        g.font = '10px system-ui'
        g.fillText(label, x0 + 5, yWave + 12)
      }
    }

    // Corte estreito demais para desenhar vira um risco na régua de cortes,
    // no rodapé da onda. Cento e cinquenta hachuras de 1 px em cima da onda
    // é o que fazia a trilha virar um borrão vermelho.
    const CUT_BAR = 5
    for (const r of view.removed ?? []) {
      const x0 = toX(r.start); const x1 = toX(r.end)
      if (x1 < 0 || x0 > size.w || x1 - x0 < 0.15) continue
      if (r.reason === 'palma') continue      // o take já desenha esse trecho
      if (x1 - x0 < 3) {
        g.fillStyle = 'rgba(239,120,120,0.85)'
        g.fillRect(x0, yWave + ROW.wave - CUT_BAR, Math.max(1, x1 - x0), CUT_BAR)
        continue
      }
      drawGone(x0, x1, 'rgba(127,29,29,0.22)', 'rgba(220,120,120,0.55)',
               r.reason === 'silencio' ? 'silêncio cortado' : 'trecho removido')
    }
    for (const take of view.takes ?? []) {
      if (take.restored) continue
      const x0 = toX(take.start); const x1 = toX(take.end)
      if (x1 < 0 || x0 > size.w) continue
      drawGone(x0, x1, 'rgba(100,116,139,0.26)', 'rgba(190,203,220,0.65)',
               'take descartado (palma)')
    }

    // faixas de seção
    for (const band of bands) {
      const x0 = Math.max(0, toX(band.start)); const x1 = Math.min(size.w, toX(band.end))
      if (x1 <= 0 || x0 >= size.w) continue
      const color = SECTIONS[band.section]?.color ?? '#64748b'
      g.fillStyle = color + '55'
      g.fillRect(x0, ySections + 2, x1 - x0, ROW.sections - 4)
      g.fillStyle = color
      g.fillRect(x0, ySections + 2, Math.min(2.5, x1 - x0), ROW.sections - 4)
      const label = SECTIONS[band.section]?.label ?? band.section
      g.font = '10px system-ui'
      if (x1 - x0 > g.measureText(label).width + 14) {
        g.fillStyle = '#e6edf7'
        g.save(); g.beginPath()
        g.rect(x0, ySections, x1 - x0 - 2, ROW.sections); g.clip()
        g.fillText(label, x0 + 6, ySections + 11)
        g.restore()
      }
    }

    // blocos, coloridos pela VELOCIDADE
    for (const b of view.blocks) {
      if (b.source !== 'main') continue
      const x0 = toX(b.src_start); const x1 = toX(b.src_end)
      if (x1 < 0 || x0 > size.w) continue
      const w = Math.max(1, x1 - x0)
      const sel = b.id === selectedClip
      const color = speedColor(b.speed)
      g.fillStyle = color + (sel ? 'ff' : 'bb')
      g.fillRect(x0, yBlocks + 3, w, ROW.blocks - 8)
      if (sel) {
        g.strokeStyle = '#f8fafc'; g.lineWidth = 2
        g.strokeRect(x0 + 1, yBlocks + 4, w - 2, ROW.blocks - 10)
      }
      if (w > 30) {
        g.fillStyle = '#08111f'
        g.font = 'bold 10px ui-monospace, monospace'
        g.fillText(`${b.speed.toFixed(2)}x`, x0 + 4, yBlocks + 15)
      }
      // bloco com enquadramento fechado ganha um canto marcado: dá para ver
      // o jogo de zoom da timeline inteira sem clicar em nada
      if ((b.zoom ?? 1) > 1.001) {
        g.fillStyle = '#08111f'
        g.beginPath()
        g.moveTo(x1 - Math.min(9, w), yBlocks + 3)
        g.lineTo(x1, yBlocks + 3); g.lineTo(x1, yBlocks + 12)
        g.closePath(); g.fill()
      }
      // corte de verdade: um talho escuro entre os blocos
      g.fillStyle = '#0f1218'
      if (b.cut_in) g.fillRect(x0 - 1, yBlocks + 1, 2.5, ROW.blocks - 4)
      if (b.cut_out) g.fillRect(x1 - 1.5, yBlocks + 1, 2.5, ROW.blocks - 4)
    }

    // legendas
    g.font = '9px system-ui'
    for (const s of subsOnSource) {
      const live = subDrag && subDrag.id === s.cue.id
      const sStart = live && subDrag.side === 'start' ? subDrag.t : s.start
      const sEnd = live && subDrag.side === 'end' ? subDrag.t : s.end
      const x0 = toX(sStart); const x1 = toX(sEnd)
      if (x1 < 0 || x0 > size.w) continue
      const w = Math.max(1, x1 - x0)
      g.fillStyle = 'rgba(56,189,248,0.22)'
      g.fillRect(x0, ySubs + 3, w, ROW.subs - 8)
      if (w < 6) continue          // afastado demais: só a barra, sem contorno
      g.strokeStyle = 'rgba(56,189,248,0.5)'
      g.strokeRect(x0 + 0.5, ySubs + 3.5, w - 1, ROW.subs - 9)
      if (w > 40) {
        g.fillStyle = '#bae6fd'
        g.save(); g.beginPath()
        g.rect(x0 + 2, ySubs, w - 4, ROW.subs); g.clip()
        g.fillText(s.cue.text.replace('\n', ' '), x0 + 4, ySubs + 14)
        g.restore()
      }
      g.fillStyle = live ? '#38bdf8' : 'rgba(56,189,248,0.85)'
      g.fillRect(x0, ySubs + 3, 2, ROW.subs - 8)
      g.fillRect(x1 - 2, ySubs + 3, 2, ROW.subs - 8)
    }

    // marcas, na faixa própria: nada disso encosta na onda
    for (const issue of view.audit ?? []) {
      const x = toX(issue.time)
      if (x < -6 || x > size.w + 6) continue
      g.fillStyle = '#ef4444'
      g.beginPath()
      g.moveTo(x, yMarks + ROW.marks - 1)
      g.lineTo(x - 5, yMarks + 3); g.lineTo(x + 5, yMarks + 3)
      g.closePath(); g.fill()
    }
    for (const clap of view.claps ?? []) {
      const x = toX(clap.time)
      if (x < -8 || x > size.w + 8) continue
      const color = clap.enabled ? '#f59e0b' : (clap.suspect ? '#facc15' : '#475569')
      g.fillStyle = color
      // bandeirinha: forma diferente do triângulo do alerta
      g.fillRect(x - 0.5, yMarks + 2, 1.5, ROW.marks - 3)
      g.beginPath()
      g.moveTo(x + 1, yMarks + 2); g.lineTo(x + 9, yMarks + 5)
      g.lineTo(x + 1, yMarks + 8); g.closePath(); g.fill()
      if (!clap.enabled) {
        g.strokeStyle = '#0f1218'; g.lineWidth = 1
        g.beginPath(); g.moveTo(x - 3, yMarks + 8); g.lineTo(x + 9, yMarks + 2); g.stroke()
      }
    }
  }, [size, height, start, span, envelope, wavePeaks, view, selectedClip,
      subsOnSource, subDrag, bands, toX, yRuler, yMarks, yWave, ySections,
      yBlocks, ySubs])

  // ------------------------------------------- camada viva (60 quadros/s)
  const drawOverlay = useCallback(() => {
    const cv = over.current
    if (!cv) return
    const dpr = window.devicePixelRatio || 1
    if (cv.width !== Math.round(size.w * dpr) || cv.height !== Math.round(height * dpr)) {
      cv.width = size.w * dpr; cv.height = height * dpr
      cv.style.width = `${size.w}px`; cv.style.height = `${height}px`
    }
    const g = cv.getContext('2d')!
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    g.clearRect(0, 0, size.w, height)

    if (selection) {
      const x0 = toX(Math.min(selection.start, selection.end))
      const x1 = toX(Math.max(selection.start, selection.end))
      g.fillStyle = 'rgba(56,189,248,0.16)'
      g.fillRect(x0, yWave, x1 - x0, ROW.wave + ROW.sections + ROW.blocks)
      g.strokeStyle = '#38bdf8'; g.lineWidth = 1
      g.beginPath()
      g.moveTo(x0 + 0.5, yWave); g.lineTo(x0 + 0.5, yBlocks + ROW.blocks)
      g.moveTo(x1 - 0.5, yWave); g.lineTo(x1 - 0.5, yBlocks + ROW.blocks)
      g.stroke()
    }

    // playhead: do tempo de SAÍDA para o eixo da fonte
    const t = getPlayhead()
    let src: number | null = null
    for (const b of view.blocks) {
      if (b.source !== 'main') continue
      const s = b.out_start ?? 0; const e = b.out_end ?? 0
      if (t >= s - 1e-6 && t <= e + 1e-6) {
        const scale = (e - s) / Math.max(b.src_duration, 1e-9)
        src = b.src_start + (t - s) / (scale || 1)
        break
      }
    }
    if (src != null) {
      const x = Math.round(toX(src)) + 0.5
      g.strokeStyle = '#f8fafc'; g.lineWidth = 1
      g.beginPath(); g.moveTo(x, yMarks); g.lineTo(x, ySubs + ROW.subs); g.stroke()
      g.fillStyle = '#f8fafc'
      g.beginPath()
      g.moveTo(x - 5, yMarks); g.lineTo(x + 5, yMarks); g.lineTo(x, yMarks + 7)
      g.closePath(); g.fill()
    }
  }, [size, height, selection, view.blocks, toX, yMarks, yWave, yBlocks, ySubs])

  useEffect(() => {
    drawOverlay()
    return subscribePlayhead(drawOverlay)
  }, [drawOverlay])

  // --------------------------------------------------------- interações
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const rect = over.current!.getBoundingClientRect()
    const x = e.clientX - rect.left
    if (e.ctrlKey || e.metaKey || !e.shiftKey) {
      const anchor = toT(x)
      const factor = Math.exp(e.deltaY * 0.0016)
      const next = clamp(span * factor, 1, total)
      const ratio = (anchor - start) / span
      setSpan(next)
      setStart(clamp(anchor - ratio * next, 0, Math.max(0, total - next)))
    } else {
      setStart((s) => clamp(s + e.deltaX * span / size.w, 0, Math.max(0, total - span)))
    }
  }

  const pos = (e: React.MouseEvent) => {
    const rect = over.current!.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  const clapNear = (x: number, y: number) => {
    if (y < yMarks - 3 || y > yMarks + ROW.marks + 3) return null
    let best: { clap: any; d: number } | null = null
    for (const c of view.claps ?? []) {
      const d = Math.abs(toX(c.time) + 4 - x)
      if (d <= 9 && (!best || d < best.d)) best = { clap: c, d }
    }
    return best?.clap ?? null
  }

  const onMouseDown = (e: React.MouseEvent) => {
    const { x, y } = pos(e)
    const t = toT(x)
    const clap = clapNear(x, y)
    if (clap && !e.altKey) {
      props.onToggleClap(clap.id, !clap.enabled)
      return
    }
    if (y >= ySubs && y < ySubs + ROW.subs && !e.altKey) {
      let best: { id: string; side: 'start' | 'end'; d: number } | null = null
      for (const s of subsOnSource) {
        for (const [side, tt] of [['start', s.start], ['end', s.end]] as const) {
          const d = Math.abs(toX(tt) - x)
          if (d <= 7 && (!best || d < best.d)) best = { id: s.cue.id, side, d }
        }
      }
      if (best) {
        drag.current = { mode: 'sub', t0: t, x0: x, s0: start, cueId: best.id }
        setSubDrag({ id: best.id, side: best.side, t })
        return
      }
    }
    if (e.button === 1 || e.altKey) {
      drag.current = { mode: 'pan', t0: t, x0: x, s0: start }
      return
    }
    if (y >= ySections && y < yBlocks + ROW.blocks) {
      const block = view.blocks.find((b) =>
        b.source === 'main' && t >= b.src_start && t <= b.src_end)
      setState({ selectedClip: block?.id ?? null })
      if (block) {
        const out = sourceToOutput(t, view.blocks)
        if (out != null) setPlayhead(out)
      }
      return
    }
    drag.current = { mode: 'select', t0: t, x0: x, s0: start }
    setState({ selection: { start: t, end: t } })
  }

  const onMouseMove = (e: React.MouseEvent) => {
    const { x, y } = pos(e)
    const t = toT(x)
    setHover({ x, t })
    ;(e.currentTarget as HTMLElement).style.cursor =
      clapNear(x, y) ? 'pointer' : 'crosshair'
    const d = drag.current
    if (!d) return
    if (d.mode === 'pan') {
      setStart(clamp(d.s0 - (x - d.x0) * span / size.w, 0, Math.max(0, total - span)))
    } else if (d.mode === 'select') {
      setState({ selection: { start: Math.min(d.t0, t), end: Math.max(d.t0, t) } })
    } else if (d.mode === 'sub') {
      setSubDrag((prev) => (prev ? { ...prev, t } : prev))
    }
  }

  const onMouseUp = (e: React.MouseEvent) => {
    const d = drag.current
    drag.current = null
    if (d?.mode === 'sub' && subDrag) {
      const out = sourceToOutput(subDrag.t, view.blocks)
      if (out != null) props.onSubtitleEdge(subDrag.id, subDrag.side, out)
      setSubDrag(null)
      return
    }
    if (d?.mode === 'select') {
      const { x } = pos(e)
      if (Math.abs(x - d.x0) < 3) {
        setState({ selection: null })
        const out = sourceToOutput(toT(x), view.blocks)
        if (out != null) setPlayhead(out)
      }
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el?.tagName === 'INPUT' || el?.tagName === 'TEXTAREA' || el?.isContentEditable) return
      if (e.key !== 'Delete' && e.key !== 'Backspace') return
      if (selection) {
        e.preventDefault()
        props.onDeleteSelection()
      } else if (selectedClip) {
        // sem área marcada, Delete apaga o BLOCO clicado — é o gesto do CapCut
        e.preventDefault()
        props.onDeleteClip(selectedClip)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selection, selectedClip, props])

  const hoveredRemoved = hover
    ? (view.removed ?? []).find((r) => hover.t >= r.start && hover.t <= r.end)
    : null
  const hoveredTake = hover
    ? (view.takes ?? []).find((t) => !t.restored && hover.t >= t.start && hover.t <= t.end)
    : null
  const cuts = view.blocks.filter((b) => b.source === 'main').length
  const fast = view.blocks.filter((b) => b.source === 'main' && b.speed > 1.25).length

  return (
    <div className="border-t border-line bg-ink-800">
      <div className="flex items-center gap-2 px-3 py-1.5 text-[11px] text-slate-500">
        <span>zoom</span>
        <input type="range" min={0} max={1000} value={zoomToSlider(span, total)}
               className="w-40"
               onChange={(e) => {
                 const next = sliderToZoom(Number(e.target.value), total)
                 const center = start + span / 2
                 setSpan(next)
                 setStart(clamp(center - next / 2, 0, Math.max(0, total - next)))
               }} />
        <span className="font-mono w-20">{span < 60 ? `${span.toFixed(1)} s` : timecode(span)}</span>
        <button className="btn btn-xs" onClick={() => { setSpan(total); setStart(0) }}>
          vídeo inteiro
        </button>
        <span className="text-slate-600">
          {cuts} blocos{fast ? ` · ${fast} acima de 1,25x` : ''}
        </span>
        <span className="ml-auto font-mono">
          {hover ? `fonte ${timecode(hover.t, true)}` : ''}
        </span>
        {hoveredTake && (
          <button className="btn btn-xs"
                  onClick={() => props.onToggleTake(hoveredTake.id, true)}>
            recuperar este take
          </button>
        )}
        {hoveredRemoved && !hoveredTake && hoveredRemoved.reason !== 'palma' && (
          <button className="btn btn-xs"
                  onClick={() => props.onRestore(hoveredRemoved.start, hoveredRemoved.end)}>
            recuperar trecho
          </button>
        )}
      </div>
      <div ref={wrap} className="relative select-none" style={{ height }}>
        <canvas ref={base} className="absolute inset-0 block" />
        <canvas ref={over}
                className="absolute inset-0 block cursor-crosshair"
                onWheel={onWheel}
                onMouseDown={onMouseDown}
                onMouseMove={onMouseMove}
                onMouseUp={onMouseUp}
                onMouseLeave={() => {
                  setHover(null)
                  if (drag.current?.mode === 'sub' && subDrag) {
                    // o mouse escapou do canvas no meio do arrasto: comita o
                    // ajuste em vez de descartá-lo em silêncio
                    const out = sourceToOutput(subDrag.t, view.blocks)
                    if (out != null) props.onSubtitleEdge(subDrag.id, subDrag.side, out)
                    setSubDrag(null)
                  }
                  drag.current = null
                }} />
      </div>
      <div className="flex items-center gap-3 px-3 py-1.5 text-[10px] text-slate-500
                      border-t border-line flex-wrap">
        <span className="text-slate-600">velocidade:</span>
        {[['#3b82f6', '1,00x'], ['#22c55e', 'até 1,12x'], ['#eab308', 'até 1,25x'],
          ['#f97316', 'acima de 1,25x']].map(([c, l]) => (
          <span key={l} className="flex items-center gap-1">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: c }} />{l}
          </span>
        ))}
        <span className="text-slate-600 ml-2">seção:</span>
        {Object.entries(SECTIONS).map(([k, v]) => (
          <span key={k} className="flex items-center gap-1">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: v.color }} />
            {v.label}
          </span>
        ))}
        <span className="flex items-center gap-1 ml-2">
          <span className="w-2.5 h-2.5 rounded-sm bg-red-900/60 border border-red-400/50" />
          sai do vídeo
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-1 rounded-sm bg-red-400" />corte (dá zoom para ver)
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm bg-amber-500" />palma (clique desliga)
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 bg-slate-300"
                style={{ clipPath: 'polygon(100% 0,100% 100%,0 0)' }} />
          enquadramento fechado
        </span>
        <span className="ml-auto">
          clique num bloco e Delete apaga ele · arraste para marcar um trecho e Delete corta · roda dá zoom · Alt+arraste move
        </span>
      </div>
    </div>
  )
}

function niceStep(span: number, width: number): number {
  const target = span / Math.max(3, width / 90)
  const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800]
  return steps.find((s) => s >= target) ?? 3600
}

function zoomToSlider(span: number, total: number): number {
  const min = Math.log(1); const max = Math.log(Math.max(total, 2))
  return 1000 - Math.round((Math.log(clamp(span, 1, total)) - min) / (max - min) * 1000)
}

function sliderToZoom(value: number, total: number): number {
  const min = Math.log(1); const max = Math.log(Math.max(total, 2))
  return clamp(Math.exp(min + (1000 - value) / 1000 * (max - min)), 1, total)
}
