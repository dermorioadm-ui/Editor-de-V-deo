import { useCallback, useEffect, useRef, useState } from 'react'
import type { Clip, SubtitleCue } from '../types'
import { timecode } from '../lib/format'
import { blockAtOutput, cueAt, outputToSource } from '../lib/timeline'
import { api } from '../lib/api'
import { setState, useStore } from '../state/store'

interface Props {
  projectId: string
  blocks: Clip[]
  cues: SubtitleCue[]
  duration: number
  style: any
  safeZone?: { top: number; bottom: number } | null
  previewUrl?: string | null
  onRequestPreview?: () => void
  previewBusy?: boolean
}

/**
 * Prévia sem renderizar (Parte 6.4): toca o arquivo original pulando os
 * trechos removidos, aplicando a velocidade de cada bloco e desenhando a
 * legenda por cima.
 */
export default function Player({ projectId, blocks, cues, duration, style, safeZone,
                                 previewUrl, onRequestPreview, previewBusy }: Props) {
  const video = useRef<HTMLVideoElement>(null)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const playhead = useStore((s) => s.playhead)
  const seekingRef = useRef(false)
  const rafRef = useRef<number>()
  // bloco não-main (foto/inserto) tocando "no relógio": {clip, perfStart, offset0}
  const stillRef = useRef<{ clip: Clip; perfStart: number; offset0: number } | null>(null)
  const [stillClip, setStillClip] = useState<Clip | null>(null)

  const enterStill = useCallback((clip: Clip, offset: number) => {
    stillRef.current = { clip, perfStart: performance.now(), offset0: offset }
    setStillClip(clip)
    video.current?.pause()
  }, [])

  const leaveStill = useCallback(() => {
    stillRef.current = null
    setStillClip(null)
  }, [])

  const linear = !!previewUrl

  const seekOutput = useCallback((t: number) => {
    const el = video.current
    if (!el) return
    if (linear) {
      el.currentTime = Math.max(0, Math.min(t, duration - 0.01))
      setState({ playhead: el.currentTime })
      return
    }
    if (!blocks.length) return
    const clamped = Math.max(0, Math.min(t, duration - 0.01))
    const block = blockAtOutput(clamped, blocks)
    if (block && block.source !== 'main') {
      // caiu numa foto ou num inserto: mostra o quadro da própria mídia
      enterStill(block, clamped - (block.out_start ?? 0))
      setState({ playhead: clamped })
      return
    }
    const wasStill = stillRef.current !== null
    leaveStill()
    const pos = outputToSource(clamped, blocks)
    if (!pos) return
    seekingRef.current = true
    el.currentTime = pos.time
    if (block) el.playbackRate = block.speed
    setState({ playhead: clamped })
    // se estava tocando um still e o alvo é vídeo, retoma o vídeo — senão a
    // reprodução ficava congelada com o botão dizendo "pausa"
    if (wasStill && playing) el.play().catch(() => {})
    window.setTimeout(() => { seekingRef.current = false }, 60)
  }, [blocks, duration, linear, enterStill, leaveStill, playing])

  // segue o playhead vindo da timeline
  useEffect(() => {
    const el = video.current
    if (!el || seekingRef.current || playing) return
    if (linear) {
      if (Math.abs(el.currentTime - playhead) > 0.25) el.currentTime = playhead
      return
    }
    const pos = outputToSource(playhead, blocks)
    if (pos && Math.abs(el.currentTime - pos.time) > 0.25) {
      el.currentTime = pos.time
    }
  }, [playhead, blocks, playing, linear])

  // laço de reprodução: pula os buracos e ajusta a velocidade por bloco
  useEffect(() => {
    if (!playing) return
    const advance = (outT: number) => {
      // entrega a reprodução ao bloco que contém outT (vídeo ou "still")
      const el = video.current!
      const block = blockAtOutput(outT, blocks)
      if (!block || outT >= duration - 0.02) {
        el.pause()
        leaveStill()
        setPlaying(false)
        setState({ playhead: duration })
        return
      }
      if (block.source === 'main') {
        leaveStill()
        el.currentTime = block.src_start +
          (outT - (block.out_start ?? 0)) * block.speed
        el.playbackRate = block.speed
        el.play().catch(() => {})
      } else {
        enterStill(block, outT - (block.out_start ?? 0))
      }
    }
    const tick = () => {
      const el = video.current
      const still = stillRef.current
      if (el && linear) {
        setState({ playhead: el.currentTime })
      } else if (el && still && playing) {
        // foto/inserto: o relógio é o performance.now()
        const elapsed = still.offset0 + (performance.now() - still.perfStart) / 1000
        const dur = (still.clip.out_end ?? 0) - (still.clip.out_start ?? 0)
        if (elapsed >= dur) {
          advance((still.clip.out_end ?? 0) + 0.001)
        } else {
          setState({ playhead: (still.clip.out_start ?? 0) + elapsed })
        }
      } else if (el && blocks.length) {
        const src = el.currentTime
        let current: Clip | null = null
        for (const b of blocks) {
          if (b.source !== 'main') continue
          if (src >= b.src_start - 0.02 && src < b.src_end) { current = b; break }
        }
        if (!current) {
          // fim do bloco main: o próximo na ORDEM DE SAÍDA decide (pode ser foto)
          const prev = [...blocks].filter((b) => b.source === 'main' && b.src_end <= src + 0.05)
            .sort((a, b) => (b.out_end ?? 0) - (a.out_end ?? 0))[0]
          advance(prev ? (prev.out_end ?? 0) + 0.001 : duration)
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
  }, [playing, blocks, duration, linear, enterStill, leaveStill])

  const toggle = () => {
    const el = video.current
    if (!el) return
    if (playing) {
      el.pause()
      if (stillRef.current) {
        // congela o relógio do still no ponto atual
        stillRef.current.offset0 = playhead - (stillRef.current.clip.out_start ?? 0)
        stillRef.current.perfStart = performance.now()
      }
      setPlaying(false)
    } else {
      if (playhead >= duration - 0.05) {
        // fim natural: tocar de novo recomeça do zero
        seekOutput(0)
        window.setTimeout(() => {
          const b = !linear ? blockAtOutput(0, blocks) : null
          if (b && b.source !== 'main') { setPlaying(true); return }
          video.current?.play().then(() => setPlaying(true)).catch(() => {})
        }, 80)
        return
      }
      const block = !linear ? blockAtOutput(playhead, blocks) : null
      if (block && block.source !== 'main') {
        enterStill(block, playhead - (block.out_start ?? 0))
        setPlaying(true)
        return
      }
      if (!linear) {
        const pos = outputToSource(playhead, blocks)
        if (pos) el.currentTime = pos.time
      }
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
               key={previewUrl ?? 'source'}
               src={previewUrl ?? `/api/projects/${projectId}/source`}
               onPause={() => { if (!stillRef.current) setPlaying(false) }} />
        {stillClip && !linear && (
          <div className="absolute inset-0 bg-black grid place-items-center">
            <img className="max-h-full max-w-full object-contain"
                 src={api.frameUrl(projectId,
                   stillClip.kind === 'photo' ? 0.0
                     : stillClip.src_start +
                       Math.floor(Math.max(0, playhead - (stillClip.out_start ?? 0))
                                  * stillClip.speed * 2) / 2,
                   stillClip.source, 540)}
                 alt="" />
            <span className="absolute top-1.5 left-1.5 chip border-line
                             text-slate-300 bg-ink-900/80">
              {stillClip.kind === 'photo' ? 'foto inserida' : 'inserto (sem áudio na prévia)'}
            </span>
          </div>
        )}
        {linear && (
          <span className="absolute top-1.5 left-1.5 chip border-accent/60
                           text-accent bg-ink-900/80">
            prévia 480p renderizada
          </span>
        )}
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
        {cue && !linear && (
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
      <div className="flex items-center gap-2">
        <p className="hint flex-1">
          {linear
            ? 'Tocando a prévia renderizada em 480p. A exportação final continua em '
              + 'qualidade cheia.'
            : 'Prévia com cortes, velocidades e legendas aplicados — nada é '
              + 'renderizado. Espaço toca/pausa, setas movem 0,1 s (1 s com Shift).'}
        </p>
        {onRequestPreview && (
          <button className="btn btn-xs shrink-0" disabled={previewBusy}
                  onClick={onRequestPreview}>
            {previewBusy ? 'renderizando…' : (linear ? 'refazer prévia' : 'prévia 480p')}
          </button>
        )}
      </div>
    </div>
  )
}
