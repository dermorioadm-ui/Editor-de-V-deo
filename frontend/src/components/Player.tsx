import { useCallback, useEffect, useRef, useState } from 'react'
import type { Clip, SubtitleCue } from '../types'
import { timecode } from '../lib/format'
import { blockAtOutput, cueAt, outputToSource } from '../lib/timeline'
import { setState, useStore } from '../state/store'

interface Props {
  projectId: string
  blocks: Clip[]
  cues: SubtitleCue[]
  duration: number
  style: any
  safeZone?: { top: number; bottom: number } | null
}

/**
 * Prévia sem renderizar (Parte 6.4): toca o arquivo original pulando os
 * trechos removidos, aplicando a velocidade de cada bloco e desenhando a
 * legenda por cima.
 */
export default function Player({ projectId, blocks, cues, duration, style, safeZone }: Props) {
  const video = useRef<HTMLVideoElement>(null)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const playhead = useStore((s) => s.playhead)
  const seekingRef = useRef(false)
  const rafRef = useRef<number>()

  const seekOutput = useCallback((t: number) => {
    const el = video.current
    if (!el || !blocks.length) return
    const clamped = Math.max(0, Math.min(t, duration - 0.01))
    const pos = outputToSource(clamped, blocks)
    if (!pos) return
    seekingRef.current = true
    el.currentTime = pos.time
    const block = blockAtOutput(clamped, blocks)
    if (block) el.playbackRate = block.speed
    setState({ playhead: clamped })
    window.setTimeout(() => { seekingRef.current = false }, 60)
  }, [blocks, duration])

  // segue o playhead vindo da timeline
  useEffect(() => {
    const el = video.current
    if (!el || seekingRef.current || playing) return
    const pos = outputToSource(playhead, blocks)
    if (pos && Math.abs(el.currentTime - pos.time) > 0.25) {
      el.currentTime = pos.time
    }
  }, [playhead, blocks, playing])

  // laço de reprodução: pula os buracos e ajusta a velocidade por bloco
  useEffect(() => {
    if (!playing) return
    const tick = () => {
      const el = video.current
      if (el && blocks.length) {
        const src = el.currentTime
        let current: Clip | null = null
        for (const b of blocks) {
          if (b.source !== 'main') continue
          if (src >= b.src_start - 0.02 && src < b.src_end) { current = b; break }
        }
        if (!current) {
          const next = blocks.find((b) => b.source === 'main' && b.src_start > src)
          if (next) {
            el.currentTime = next.src_start
            el.playbackRate = next.speed
          } else {
            el.pause()
            setPlaying(false)
            setState({ playhead: duration })
          }
        } else {
          if (Math.abs(el.playbackRate - current.speed) > 0.005) {
            el.playbackRate = current.speed
          }
          const scale = ((current.out_end ?? 0) - (current.out_start ?? 0)) /
            Math.max(current.src_duration, 1e-9)
          setState({ playhead: (current.out_start ?? 0) + (src - current.src_start) * scale })
        }
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [playing, blocks, duration])

  const toggle = () => {
    const el = video.current
    if (!el) return
    if (playing) { el.pause(); setPlaying(false) } else {
      const pos = outputToSource(playhead, blocks)
      if (pos) el.currentTime = pos.time
      el.play().then(() => setPlaying(true)).catch(() => {})
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' ||
          target?.isContentEditable) return
      if (e.code === 'Space') { e.preventDefault(); toggle() }
      if (e.code === 'ArrowLeft') seekOutput(playhead - (e.shiftKey ? 1 : 0.1))
      if (e.code === 'ArrowRight') seekOutput(playhead + (e.shiftKey ? 1 : 0.1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const cue = cueAt(playhead, cues)
  const block = blockAtOutput(playhead, blocks)

  return (
    <div className="flex flex-col gap-2 h-full min-h-0">
      <div className="relative flex-1 min-h-0 bg-black rounded-lg overflow-hidden
                      border border-line grid place-items-center">
        <video ref={video} className="max-h-full max-w-full" playsInline muted={muted}
               src={`/api/projects/${projectId}/source`}
               onPause={() => setPlaying(false)} />
        {safeZone && (
          <div className="absolute left-0 right-0 border-y border-dashed
                          border-amber-500/50 bg-amber-500/5 pointer-events-none"
               style={{ top: `${safeZone.top * 100}%`,
                        height: `${(safeZone.bottom - safeZone.top) * 100}%` }}>
            <span className="absolute right-1 top-1 text-[9px] text-amber-400/80">
              zona da legenda
            </span>
          </div>
        )}
        {cue && (
          <div className="absolute left-0 right-0 pointer-events-none px-[6%] text-center"
               style={{ bottom: `${(style?.margin_v ?? 220) / 1920 * 100}%` }}>
            <span className="inline-block whitespace-pre-line leading-tight"
                  style={{
                    fontSize: `clamp(11px, ${(style?.fontsize ?? 64) / 1920 * 100}vh, 46px)`,
                    fontWeight: style?.bold ? 800 : 500,
                    color: style?.primary ?? '#fff',
                    WebkitTextStroke: `${Math.max(1, (style?.outline ?? 4) / 2.6)}px ${style?.outline_color ?? '#000'}`,
                    paintOrder: 'stroke fill',
                    textTransform: style?.uppercase ? 'uppercase' : 'none',
                  }}>
              {cue.text}
            </span>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 text-xs">
        <button className="btn btn-xs w-16" onClick={toggle}>
          {playing ? '❚❚ pausa' : '▶ tocar'}
        </button>
        <span className="font-mono text-slate-400">
          {timecode(playhead, true)} / {timecode(duration)}
        </span>
        {block && (
          <span className="chip border-line text-slate-400">
            {block.speed.toFixed(2)}x
          </span>
        )}
        <button className="btn btn-xs ml-auto" onClick={() => setMuted((m) => !m)}>
          {muted ? 'som off' : 'som on'}
        </button>
      </div>
      <input type="range" min={0} max={Math.max(duration, 0.1)} step={0.01}
             value={playhead} className="w-full"
             onChange={(e) => seekOutput(Number(e.target.value))} />
      <p className="hint">
        Prévia com cortes, velocidades e legendas aplicados — nada é renderizado.
        Espaço toca/pausa, setas movem 0,1 s (1 s com Shift).
      </p>
    </div>
  )
}
