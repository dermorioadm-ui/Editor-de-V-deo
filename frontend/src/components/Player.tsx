import { useCallback, useEffect, useRef, useState } from 'react'
import type { Clip, SubtitleCue } from '../types'
import { timecode } from '../lib/format'
import { blockAtOutput, cueAt, outputToSource } from '../lib/timeline'
import { api } from '../lib/api'
import { setPlayhead, setState, usePlayhead, useStore } from '../state/store'

interface Props {
  projectId: string
  blocks: Clip[]
  /** Resolução da FONTE, [largura, altura]. É a régua em que fontsize, margem
   *  e contorno do estilo estão escritos — e a mesma que o ASS usa como
   *  PlayRes na exportação. NÃO é a resolução do que está tocando: com a
   *  prévia leve, o elemento de vídeo tem 854 de altura contra 1920 da fonte,
   *  e usar a dele deixava a legenda 2,25x maior. */
  sourceSize?: [number, number] | null
  /** Centro do rosto (0..1), medido na análise. É a âncora do jogo de
   *  câmeras: o preview aplica o MESMO recorte concêntrico da exportação,
   *  via transform de CSS — de graça, sem renderizar nada. */
  zoomAnchor?: { x: number; y: number } | null
  cues: SubtitleCue[]
  duration: number
  style: any
  safeZone?: { top: number; bottom: number } | null
  previewUrl?: string | null
  onRequestPreview?: () => void
  previewBusy?: boolean
  // cópia leve da FONTE, para tocar sem engasgo. A linha do tempo é idêntica
  // à do original, então nada no cálculo de tempo muda.
  proxyUrl?: string | null
}

/**
 * Prévia sem renderizar (Parte 6.4): toca o arquivo original pulando os
 * trechos removidos, aplicando a velocidade de cada bloco e desenhando a
 * legenda por cima.
 */
export default function Player({ projectId, blocks, cues, duration, style, safeZone,
                                sourceSize, zoomAnchor,
                                 previewUrl, onRequestPreview, previewBusy,
                                 proxyUrl }: Props) {
  const video = useRef<HTMLVideoElement>(null)
  // "tocando" mora na store: a timeline também tem play/pause, e os dois
  // precisam mostrar o mesmo estado
  const playing = useStore((s) => s.playing)
  const setPlaying = (v: boolean) => setState({ playing: v })
  const playRequest = useStore((s) => s.playRequest)
  const [muted, setMuted] = useState(false)
  const playhead = usePlayhead()
  const seekingRef = useRef(false)
  const rafRef = useRef<number>()
  // bloco não-main (foto/inserto) tocando "no relógio": {clip, perfStart, offset0}
  const stillRef = useRef<{ clip: Clip; perfStart: number; offset0: number } | null>(null)
  const [stillClip, setStillClip] = useState<Clip | null>(null)
  // A caixa da IMAGEM dentro do player, em pixels. A legenda tinha o tamanho
  // calculado em `vh` — altura da JANELA — e ocupava a largura do PAINEL. Num
  // painel de 320px com o vídeo pequeno dentro, isso dava uma fonte ~4x maior
  // e o texto quebrava em 4 linhas onde a exportação faz 2.
  const [box, setBox] = useState({ left: 0, top: 0, width: 0, height: 0, vh: 1920 })
  const palco = useRef<HTMLDivElement | null>(null)

  const enterStill = useCallback((clip: Clip, offset: number) => {
    stillRef.current = { clip, perfStart: performance.now(), offset0: offset }
    setStillClip(clip)
    video.current?.pause()
  }, [])

  const leaveStill = useCallback(() => {
    stillRef.current = null
    setStillClip(null)
  }, [])

  useEffect(() => {
    const el = video.current
    if (!el) return
    const medir = () => {
      const cont = palco.current
      if (!cont) return
      // a proporção vem da FONTE quando o metadata ainda não chegou: assim o
      // quadro já nasce no lugar certo em vez de pular quando o vídeo carrega
      const vw = el.videoWidth || sourceSize?.[0] || 1080
      const vh = el.videoHeight || sourceSize?.[1] || 1920
      const rc = cont.getBoundingClientRect()
      // 'contain' calculado a partir do PALCO, não do elemento: o vídeo agora
      // vive dentro de uma moldura com overflow escondido (é ela que faz o
      // zoom parecer recorte, não crescimento), então o elemento não serve
      // mais de régua para si mesmo
      const escala = Math.min(rc.width / vw, rc.height / vh)
      const w = vw * escala
      const h = vh * escala
      setBox((b) => {
        const novo = { left: (rc.width - w) / 2, top: (rc.height - h) / 2,
                       width: w, height: h, vh }
        return Math.abs(b.width - w) < 0.5 && Math.abs(b.height - h) < 0.5
          && Math.abs(b.left - novo.left) < 0.5 ? b : novo
      })
    }
    medir()
    const ro = new ResizeObserver(medir)
    if (palco.current) ro.observe(palco.current)
    el.addEventListener('loadedmetadata', medir)
    el.addEventListener('resize', medir)
    return () => {
      ro.disconnect()
      el.removeEventListener('loadedmetadata', medir)
      el.removeEventListener('resize', medir)
    }
  }, [previewUrl, proxyUrl, projectId, sourceSize?.[0], sourceSize?.[1]])

  const linear = !!previewUrl

  const seekOutput = useCallback((t: number) => {
    const el = video.current
    if (!el) return
    if (linear) {
      el.currentTime = Math.max(0, Math.min(t, duration - 0.01))
      setPlayhead(el.currentTime)
      return
    }
    if (!blocks.length) return
    const clamped = Math.max(0, Math.min(t, duration - 0.01))
    const block = blockAtOutput(clamped, blocks)
    if (block && block.source !== 'main') {
      // caiu numa foto ou num inserto: mostra o quadro da própria mídia
      enterStill(block, clamped - (block.out_start ?? 0))
      setPlayhead(clamped)
      return
    }
    const wasStill = stillRef.current !== null
    leaveStill()
    const pos = outputToSource(clamped, blocks)
    if (!pos) return
    seekingRef.current = true
    el.currentTime = pos.time
    if (block) el.playbackRate = block.speed
    setPlayhead(clamped)
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
        setPlayhead(duration)
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
        setPlayhead(el.currentTime)
      } else if (el && still && playing) {
        // foto/inserto: o relógio é o performance.now()
        const elapsed = still.offset0 + (performance.now() - still.perfStart) / 1000
        const dur = (still.clip.out_end ?? 0) - (still.clip.out_start ?? 0)
        if (elapsed >= dur) {
          advance((still.clip.out_end ?? 0) + 0.001)
        } else {
          setPlayhead((still.clip.out_start ?? 0) + elapsed)
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
          setPlayhead((current.out_start ?? 0) + (src - current.src_start) * scale)
        }
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [playing, blocks, duration, linear, enterStill, leaveStill])

  const toggleRef = useRef<() => void>(() => {})
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

  toggleRef.current = toggle

  // a timeline (ou qualquer outro lugar) pede play/pause incrementando o
  // contador; o player é quem sabe tocar
  const primeiro = useRef(true)
  useEffect(() => {
    if (primeiro.current) { primeiro.current = false; return }
    toggleRef.current()
  }, [playRequest])

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

  // O JOGO DE CÂMERAS, ao vivo. O bloco atual carrega o zoom que a exportação
  // vai aplicar; aqui ele vira transform de CSS com a MESMA âncora concêntrica
  // do render: o centro alcançável é clamp(face, 1/(2z), 1-1/(2z)) — abaixo
  // disso o recorte sairia do quadro. A troca é seca, no corte, igual ao
  // arquivo final. A prévia 480p renderizada já vem com o zoom queimado, e as
  // fotos/insertos não têm zoom — nos dois casos o transform desliga.
  const zoomCss = (() => {
    const z = !linear && !stillClip ? (block?.zoom ?? 1.0) : 1.0
    if (z <= 1.001) return undefined
    const alcance = 1 / (2 * z)
    const cx = Math.min(1 - alcance, Math.max(alcance, zoomAnchor?.x ?? 0.5))
    const cy = Math.min(1 - alcance, Math.max(alcance, zoomAnchor?.y ?? 0.4))
    return {
      transform: `scale(${z})`,
      transformOrigin: `${(cx * 100).toFixed(2)}% ${(cy * 100).toFixed(2)}%`,
    } as const
  })()

  return (
    <div className="flex flex-col gap-2 h-full min-h-0">
      <div ref={palco} className="relative flex-1 min-h-0 bg-black rounded-lg
                      overflow-hidden border border-line">
        {/* a MOLDURA do quadro: exatamente o retângulo da imagem, com
            overflow escondido. É ela que transforma o scale do zoom em
            RECORTE — sem ela o vídeo parecia crescer sobre as tarjas em vez
            de fechar o plano. */}
        <div className="absolute overflow-hidden"
             style={box.width > 0
               ? { left: box.left, top: box.top,
                   width: box.width, height: box.height }
               : { inset: 0 }}>
          <video ref={video} className="w-full h-full object-contain" playsInline
                 muted={muted}
                 style={zoomCss}
                 key={previewUrl ?? proxyUrl ?? 'source'}
                 src={previewUrl ?? proxyUrl ?? `/api/projects/${projectId}/source`}
                 onPause={() => { if (!stillRef.current) setPlaying(false) }} />
        </div>
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
        {!linear && proxyUrl && (
          <span className="absolute top-1.5 left-1.5 chip border-emerald-800/70
                           text-emerald-300 bg-ink-900/80"
                title="Tocando uma cópia de 480p, feia de propósito, para a edição
                       não engasgar. A exportação lê o arquivo original em
                       qualidade cheia.">
            prévia leve
          </span>
        )}
        {safeZone && box.height > 0 && (
          <div className="absolute border-y border-dashed
                          border-amber-500/50 bg-amber-500/5 pointer-events-none"
               style={{ left: box.left, width: box.width,
                        top: box.top + safeZone.top * box.height,
                        height: (safeZone.bottom - safeZone.top) * box.height }}>
            <span className="absolute right-1 top-1 text-[9px] text-amber-400/80">
              zona da legenda
            </span>
          </div>
        )}
        {cue && !linear && box.height > 0 && (() => {
          // A RÉGUA É A DA FONTE. O ASS é escrito com PlayRes = resolução da
          // fonte, e é nela que fontsize, margem e contorno estão medidos.
          // Usar a altura do ELEMENTO era o bug: com a prévia leve tocando, o
          // elemento tem 854 de altura contra 1920 da fonte, e a legenda saía
          // 2,25x maior que na exportação.
          const playH = sourceSize?.[1] || box.vh || 1920
          const k = box.height / Math.max(playH, 1)
          const fs = style?.fontsize ?? 35

          // Medido com o filtro ass do próprio ffmpeg, Arial/Liberation:
          //
          //   altura de maiúscula = 0,640 x fontsize   (em 35, 50, 64, 80, 100)
          //   avanço de linha     = 1,000 x fontsize   (exato)
          //   contorno cresce     = 1,0 x outline PARA FORA de cada lado
          //   base da tinta fica  = margin_v + 0,172 x fontsize do fundo
          //
          // O fontsize do ASS NÃO é font-size de CSS: o libass imita o GDI e
          // escala a fonte para que ascent-descent caiba no fontsize, enquanto
          // o CSS escala pelo em. Para Arial isso dá 2048/2288 = 0,895 — sem
          // esse fator a prévia sai 11% maior que a exportação.
          const ASS_PARA_CSS = 0.895
          const DESCIDA = 0.172
          return (
            <div className="absolute pointer-events-none text-center"
                 style={{
                   left: box.left + (style?.margin_l ?? 60) * k,
                   width: Math.max(
                     10, box.width - ((style?.margin_l ?? 60) + (style?.margin_r ?? 60)) * k),
                   // margin_v é medido do fundo do quadro até a base da CAIXA
                   // DE LINHA; a tinta para 0,172 x fontsize acima disso
                   bottom: `calc(100% - ${box.top + box.height}px + ${
                     ((style?.margin_v ?? 220) + fs * DESCIDA) * k}px)`,
                 }}>
              <span className="inline-block"
                    style={{
                      // 'pre': o ASS está em WrapStyle 2, que NÃO quebra linha
                      // sozinho. Quem decide a quebra é o linebreak.py; o CSS
                      // não pode inventar mais nenhuma.
                      whiteSpace: 'pre',
                      fontFamily: `"${style?.font ?? 'Arial'}", Arial, Helvetica, sans-serif`,
                      fontSize: `${Math.max(6, fs * ASS_PARA_CSS * k)}px`,
                      // o avanço de linha do ASS é o fontsize cravado; usar
                      // leading-tight (1,25) esticava a legenda de 2 linhas 12%
                      lineHeight: `${Math.max(7, fs * k)}px`,
                      fontWeight: style?.bold ? 700 : 400,
                      color: style?.primary ?? '#fff',
                      // -webkit-text-stroke é CENTRADO (metade entra no glifo);
                      // o contorno do ASS cresce inteiro para fora. Daí o 2x.
                      WebkitTextStroke: `${Math.max(
                        0.5, (style?.outline ?? 4) * 2 * k)}px ${
                        style?.outline_color ?? '#000'}`,
                      paintOrder: 'stroke fill',
                      // sombra: deslocamento de S px para a direita e para
                      // baixo, que a prévia simplesmente não desenhava
                      textShadow: (style?.shadow ?? 0) > 0
                        ? `${(style.shadow ?? 0) * k}px ${(style.shadow ?? 0) * k}px 0 rgba(0,0,0,.75)`
                        : undefined,
                      textTransform: style?.uppercase ? 'uppercase' : 'none',
                    }}>
                {cue.text}
              </span>
            </div>
          )
        })()}
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
