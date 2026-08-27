import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Clip, Envelope, TimelineView } from '../types'
import { SECTIONS } from '../types'
import { clamp, timecode } from '../lib/format'
import { cuesOnSource, sourceToOutput } from '../lib/timeline'
import { setState, useStore } from '../state/store'

interface Props {
  view: TimelineView
  envelope: Envelope | null
  sourceDuration: number
  onDeleteSelection: () => void
  onRestore: (start: number, end: number) => void
  onToggleTake: (id: string, restored: boolean) => void
  onToggleClap: (id: string, enabled: boolean) => void
}

const ROW = { wave: 92, blocks: 46, subs: 24, ruler: 18 }
const PAD_TOP = 14

export default function Timeline(props: Props) {
  const { view, envelope, sourceDuration } = props
  const canvas = useRef<HTMLCanvasElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const [span, setSpan] = useState(sourceDuration || 1)
  const [start, setStart] = useState(0)
  const [size, setSize] = useState({ w: 800, h: 220 })
  const [hover, setHover] = useState<{ x: number; t: number } | null>(null)
  const drag = useRef<{ mode: string; t0: number; x0: number; s0: number } | null>(null)
  const playhead = useStore((s) => s.playhead)
  const selection = useStore((s) => s.selection)
  const selectedClip = useStore((s) => s.selectedClip)

  const total = sourceDuration || envelope?.duration || 1
  const height = PAD_TOP + ROW.ruler + ROW.wave + ROW.blocks + ROW.subs + 10

  useEffect(() => { setSpan(total); setStart(0) }, [total])

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      setSize({ w: el.clientWidth, h: height })
    })
    ro.observe(el)
    setSize({ w: el.clientWidth, h: height })
    return () => ro.disconnect()
  }, [height])

  const toX = useCallback((t: number) => (t - start) / span * size.w, [start, span, size.w])
  const toT = useCallback((x: number) => start + x / size.w * span, [start, span, size.w])

  const playheadSource = useMemo(() => {
    const blocks = view.blocks
    for (const b of blocks) {
      const s = b.out_start ?? 0
      const e = b.out_end ?? 0
      if (playhead >= s - 1e-6 && playhead <= e + 1e-6) {
        const scale = (e - s) / Math.max(b.src_duration, 1e-9)
        return b.src_start + (playhead - s) / (scale || 1)
      }
    }
    return null
  }, [playhead, view.blocks])

  const subsOnSource = useMemo(
    () => cuesOnSource(view.subtitles, view.blocks), [view.subtitles, view.blocks])

  // ------------------------------------------------------------- desenho
  useEffect(() => {
    const cv = canvas.current
    if (!cv) return
    const dpr = window.devicePixelRatio || 1
    cv.width = size.w * dpr
    cv.height = height * dpr
    cv.style.width = `${size.w}px`
    cv.style.height = `${height}px`
    const g = cv.getContext('2d')!
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    g.clearRect(0, 0, size.w, height)

    const yRuler = PAD_TOP
    const yWave = yRuler + ROW.ruler
    const yBlocks = yWave + ROW.wave
    const ySubs = yBlocks + ROW.blocks

    // régua
    g.fillStyle = '#0f1218'
    g.fillRect(0, 0, size.w, height)
    const step = niceStep(span, size.w)
    g.strokeStyle = '#1c2230'
    g.fillStyle = '#4b5563'
    g.font = '10px ui-monospace, monospace'
    g.lineWidth = 1
    for (let t = Math.ceil(start / step) * step; t < start + span; t += step) {
      const x = Math.round(toX(t)) + 0.5
      g.beginPath(); g.moveTo(x, yRuler); g.lineTo(x, height - 6); g.stroke()
      g.fillText(timecode(t), x + 3, yRuler + 10)
    }

    // forma de onda a partir do envelope
    if (envelope?.points?.length) {
      const pts = envelope.points
      const floor = envelope.noise_floor
      const top = 0
      const range = Math.max(6, top - floor)
      const mid = yWave + ROW.wave / 2
      g.fillStyle = '#1e3a5f'
      g.beginPath()
      for (let x = 0; x < size.w; x++) {
        const t = toT(x)
        const idx = clamp(Math.round(t / envelope.duration * (pts.length - 1)), 0, pts.length - 1)
        const db = pts[idx]
        const amp = clamp((db - floor) / range, 0, 1) * (ROW.wave / 2 - 4)
        g.rect(x, mid - amp, 1, amp * 2)
      }
      g.fill()
      // limiar de silêncio
      const sil = clamp((envelope.silence_threshold - floor) / range, 0, 1) * (ROW.wave / 2 - 4)
      g.strokeStyle = '#334155'
      g.setLineDash([3, 3])
      g.beginPath(); g.moveTo(0, mid - sil); g.lineTo(size.w, mid - sil)
      g.moveTo(0, mid + sil); g.lineTo(size.w, mid + sil); g.stroke()
      g.setLineDash([])
    }

    // takes descartados (cinza)
    for (const take of view.takes ?? []) {
      if (take.restored) continue
      const x0 = toX(take.start); const x1 = toX(take.end)
      if (x1 < 0 || x0 > size.w) continue
      g.fillStyle = 'rgba(148,163,184,0.20)'
      g.fillRect(x0, yWave, x1 - x0, ROW.wave)
      g.strokeStyle = 'rgba(148,163,184,0.5)'
      g.strokeRect(x0 + 0.5, yWave + 0.5, x1 - x0 - 1, ROW.wave - 1)
      g.fillStyle = '#94a3b8'
      g.font = '10px system-ui'
      if (x1 - x0 > 60) g.fillText('take descartado', x0 + 5, yWave + 13)
    }

    // regiões removidas (vermelho translúcido)
    for (const r of view.removed ?? []) {
      const x0 = toX(r.start); const x1 = toX(r.end)
      if (x1 < 0 || x0 > size.w || x1 - x0 < 0.4) continue
      g.fillStyle = r.reason === 'palma' ? 'rgba(148,163,184,0.12)' : 'rgba(248,113,113,0.16)'
      g.fillRect(x0, yWave, x1 - x0, ROW.wave)
    }

    // blocos coloridos por seção
    for (const b of view.blocks) {
      const x0 = toX(b.src_start); const x1 = toX(b.src_end)
      if (x1 < 0 || x0 > size.w) continue
      const color = SECTIONS[b.section]?.color ?? '#64748b'
      const w = Math.max(1, x1 - x0)
      g.fillStyle = color + (b.id === selectedClip ? 'ee' : '99')
      g.fillRect(x0, yBlocks + 4, w, ROW.blocks - 12)
      g.strokeStyle = b.id === selectedClip ? '#e2e8f0' : color
      g.lineWidth = b.id === selectedClip ? 2 : 1
      g.strokeRect(x0 + 0.5, yBlocks + 4.5, w - 1, ROW.blocks - 13)
      if (w > 34) {
        g.fillStyle = '#0a0c10'
        g.font = 'bold 10px ui-monospace, monospace'
        g.fillText(`${b.speed.toFixed(2)}x`, x0 + 4, yBlocks + 18)
      }
      if (w > 90) {
        g.fillStyle = 'rgba(10,12,16,0.75)'
        g.font = '9px system-ui'
        g.fillText(SECTIONS[b.section]?.label ?? b.section, x0 + 4, yBlocks + 29)
      }
      // bordas de corte real ficam marcadas
      g.fillStyle = '#0a0c10'
      if (b.cut_in) g.fillRect(x0, yBlocks + 4, 2, ROW.blocks - 12)
      if (b.cut_out) g.fillRect(x1 - 2, yBlocks + 4, 2, ROW.blocks - 12)
    }

    // faixa de legendas
    for (const s of subsOnSource) {
      const x0 = toX(s.start); const x1 = toX(s.end)
      if (x1 < 0 || x0 > size.w) continue
      g.fillStyle = 'rgba(56,189,248,0.22)'
      g.fillRect(x0, ySubs + 3, Math.max(1, x1 - x0), ROW.subs - 8)
      g.strokeStyle = 'rgba(56,189,248,0.5)'
      g.strokeRect(x0 + 0.5, ySubs + 3.5, Math.max(1, x1 - x0) - 1, ROW.subs - 9)
      if (x1 - x0 > 40) {
        g.fillStyle = '#bae6fd'
        g.font = '9px system-ui'
        const text = s.cue.text.replace('\n', ' ')
        g.save(); g.beginPath()
        g.rect(x0 + 2, ySubs, x1 - x0 - 4, ROW.subs); g.clip()
        g.fillText(text, x0 + 4, ySubs + 14)
        g.restore()
      }
    }

    // alertas de auditoria
    for (const issue of view.audit ?? []) {
      const x = toX(issue.time)
      if (x < -4 || x > size.w + 4) continue
      g.fillStyle = '#ef4444'
      g.beginPath()
      g.moveTo(x, yBlocks - 2); g.lineTo(x - 5, yBlocks - 11); g.lineTo(x + 5, yBlocks - 11)
      g.closePath(); g.fill()
    }

    // marcadores de palma
    for (const clap of view.claps ?? []) {
      const x = toX(clap.time)
      if (x < -6 || x > size.w + 6) continue
      const color = clap.enabled ? '#ef4444' : (clap.suspect ? '#facc15' : '#64748b')
      g.strokeStyle = color
      g.lineWidth = 2
      g.beginPath(); g.moveTo(x, yWave); g.lineTo(x, yWave + ROW.wave); g.stroke()
      g.fillStyle = color
      g.beginPath(); g.arc(x, yWave + 6, 4, 0, Math.PI * 2); g.fill()
    }

    // seleção
    if (selection) {
      const a = selection.start; const b = selection.end
      const x0 = toX(Math.min(a, b)); const x1 = toX(Math.max(a, b))
      g.fillStyle = 'rgba(56,189,248,0.16)'
      g.fillRect(x0, yWave, x1 - x0, ROW.wave + ROW.blocks)
      g.strokeStyle = '#38bdf8'
      g.lineWidth = 1
      g.beginPath()
      g.moveTo(x0 + 0.5, yWave); g.lineTo(x0 + 0.5, yWave + ROW.wave + ROW.blocks)
      g.moveTo(x1 - 0.5, yWave); g.lineTo(x1 - 0.5, yWave + ROW.wave + ROW.blocks)
      g.stroke()
    }

    // playhead
    if (playheadSource != null) {
      const x = Math.round(toX(playheadSource)) + 0.5
      g.strokeStyle = '#f8fafc'
      g.lineWidth = 1
      g.beginPath(); g.moveTo(x, yRuler); g.lineTo(x, height - 4); g.stroke()
      g.fillStyle = '#f8fafc'
      g.beginPath()
      g.moveTo(x - 5, yRuler); g.lineTo(x + 5, yRuler); g.lineTo(x, yRuler + 7)
      g.closePath(); g.fill()
    }
  }, [size, height, start, span, envelope, view, selection, selectedClip,
      playheadSource, subsOnSource, toX, toT])

  // --------------------------------------------------------- interações
  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const rect = canvas.current!.getBoundingClientRect()
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
    const rect = canvas.current!.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  const onMouseDown = (e: React.MouseEvent) => {
    const { x, y } = pos(e)
    const t = toT(x)
    const yBlocks = PAD_TOP + ROW.ruler + ROW.wave
    if (e.button === 1 || e.altKey) {
      drag.current = { mode: 'pan', t0: t, x0: x, s0: start }
      return
    }
    if (y >= yBlocks && y < yBlocks + ROW.blocks) {
      const block = view.blocks.find((b) => t >= b.src_start && t <= b.src_end)
      setState({ selectedClip: block?.id ?? null })
      if (block) {
        const out = sourceToOutput(t, view.blocks)
        if (out != null) setState({ playhead: out })
      }
      return
    }
    drag.current = { mode: 'select', t0: t, x0: x, s0: start }
    setState({ selection: { start: t, end: t } })
  }

  const onMouseMove = (e: React.MouseEvent) => {
    const { x } = pos(e)
    const t = toT(x)
    setHover({ x, t })
    const d = drag.current
    if (!d) return
    if (d.mode === 'pan') {
      setStart(clamp(d.s0 - (x - d.x0) * span / size.w, 0, Math.max(0, total - span)))
    } else if (d.mode === 'select') {
      setState({ selection: { start: Math.min(d.t0, t), end: Math.max(d.t0, t) } })
    }
  }

  const onMouseUp = (e: React.MouseEvent) => {
    const d = drag.current
    drag.current = null
    if (d?.mode === 'select') {
      const { x } = pos(e)
      if (Math.abs(x - d.x0) < 3) {
        setState({ selection: null })
        const out = sourceToOutput(toT(x), view.blocks)
        if (out != null) setState({ playhead: out })
      }
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el?.tagName === 'INPUT' || el?.tagName === 'TEXTAREA' || el?.isContentEditable) return
      if ((e.key === 'Delete' || e.key === 'Backspace') && selection) {
        e.preventDefault()
        props.onDeleteSelection()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selection, props])

  const hoveredRemoved = hover
    ? (view.removed ?? []).find((r) => hover.t >= r.start && hover.t <= r.end)
    : null
  const hoveredTake = hover
    ? (view.takes ?? []).find((t) => !t.restored && hover.t >= t.start && hover.t <= t.end)
    : null

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
      <div ref={wrap} className="relative select-none">
        <canvas ref={canvas}
                className="block cursor-crosshair"
                onWheel={onWheel}
                onMouseDown={onMouseDown}
                onMouseMove={onMouseMove}
                onMouseUp={onMouseUp}
                onMouseLeave={() => { setHover(null); drag.current = null }} />
      </div>
      <div className="flex items-center gap-3 px-3 py-1.5 text-[10px] text-slate-500
                      border-t border-line flex-wrap">
        {Object.entries(SECTIONS).map(([k, v]) => (
          <span key={k} className="flex items-center gap-1">
            <span className="w-2.5 h-2.5 rounded-sm" style={{ background: v.color }} />
            {v.label}
          </span>
        ))}
        <span className="flex items-center gap-1 ml-2">
          <span className="w-2.5 h-2.5 rounded-sm bg-red-400/40" />removido
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm bg-slate-400/40" />take descartado
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full bg-red-500" />palma
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full bg-yellow-400" />palma suspeita
        </span>
        <span className="ml-auto">
          arraste para selecionar · Delete corta · roda dá zoom · Alt+arraste move
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
