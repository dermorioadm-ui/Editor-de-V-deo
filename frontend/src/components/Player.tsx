import { useCallback, useEffect, useRef, useState } from 'react'
import type { Clip, SubtitleCue } from '../types'
import { timecode } from '../lib/format'
import { blockAtOutput, cueAt, outputToSource } from '../lib/timeline'
import { api } from '../lib/api'
import PipVideo from './PipVideo'
import { getState, setPlayhead, setState, usePlayhead, useStore } from '../state/store'

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
  /** A prévia renderizada ficou velha (você editou): o player volta para a
   *  cópia leve da fonte, que acompanha a edição na hora, enquanto a
   *  renderizada se refaz em segundo plano. */
  previaVelha?: boolean
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
  // corta o trecho marcado (início/fim) — quem executa é o Editor, que já
  // sabe encaixar a borda no vale e nunca comer palavra
  onDeleteSelection?: () => void
  // corta a FRASE que está na tela agora (a legenda corrente) — a unidade em
  // que o usuário ouve e pensa; quem executa é o Editor
  onCutCue?: (wordIds: number[]) => void
  // MEXER NO ELEMENTO EM CIMA DO VÍDEO: as sobreposições (cartão, PNG) viram
  // caixas arrastáveis na própria prévia; a cobertura vira um chip com ×.
  overlays?: any[]
  cutaways?: any[]
  media?: { id: string; name: string; kind?: string; info: any }[]
  onOverlayChange?: (id: string, patch: { x?: number; y?: number; scale?: number }) => void
  onOverlayDelete?: (id: string) => void
  onCutawayDelete?: (id: string) => void
}

/**
 * Prévia sem renderizar (Parte 6.4): toca o arquivo original pulando os
 * trechos removidos, aplicando a velocidade de cada bloco e desenhando a
 * legenda por cima.
 */
export default function Player({ projectId, blocks, cues, duration, style, safeZone,
                                sourceSize, zoomAnchor, previaVelha,
                                 previewUrl, onRequestPreview, previewBusy,
                                 proxyUrl, onDeleteSelection, onCutCue,
                                 overlays, cutaways, media,
                                 onOverlayChange, onOverlayDelete, onCutawayDelete }: Props) {
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

  // ------------------------------------------------------------------
  // O ELEMENTO EM CIMA DO VÍDEO. A geometria é a do render: o PNG ocupa
  // (largura natural × scale) pixels do quadro da FONTE, centrado em (x, y)
  // — então a caixa desenhada aqui é, em fração do quadro, exatamente onde
  // a sobreposição está queimada na prévia. Arrastar move; o canto
  // redimensiona; Delete apaga. A cobertura cobre o quadro inteiro, então
  // não há o que arrastar: ela vira um chip com o nome e um ×.
  const [selOverlay, setSelOverlay] = useState<string | null>(null)
  const [pend, setPend] = useState<{ id: string; x: number; y: number; scale: number } | null>(null)
  const pendRef = useRef<typeof pend>(null)
  const ovDrag = useRef<{ id: string; modo: 'move' | 'scale'; mx: number; my: number
                          x0: number; y0: number; s0: number; cx: number; cy: number } | null>(null)
  const fonteW = sourceSize?.[0] || 1080
  const fonteH = sourceSize?.[1] || 1920
  const ovAtivos = (overlays ?? []).filter((o: any) => o.enabled !== false
    && playhead >= o.out_start - 0.001 && playhead <= o.out_end + 0.001)
  const cutAtivo = (cutaways ?? []).find((c: any) => c.enabled !== false
    && playhead >= c.out_start && playhead <= c.out_end)
  const geo = (o: any) => {
    const p = pend && pend.id === o.id ? pend : o
    const m = (media ?? []).find((x) => x.id === o.media_id)
    // display_*: um vídeo gravado em pé tem width/height trocados pela rotação
    const mw = Number(m?.info?.display_width || m?.info?.width) || 400
    const mh = Number(m?.info?.display_height || m?.info?.height) || 200
    const fw = (mw * p.scale) / fonteW
    const fh = (mh * p.scale) / fonteH
    return {
      left: box.left + (p.x - fw / 2) * box.width,
      top: box.top + (p.y - fh / 2) * box.height,
      width: Math.max(8, fw * box.width),
      height: Math.max(8, fh * box.height),
      nome: m?.name ?? 'sobreposição', x: p.x, y: p.y, scale: p.scale,
      video: m?.kind === 'video',
    }
  }
  useEffect(() => {
    // o valor otimista do arrasto sai de cena quando o projeto já reflete a
    // mudança — sem isso a caixa pulava para trás no meio-segundo entre
    // soltar o mouse e a resposta do servidor
    if (!pend) return
    const o = (overlays ?? []).find((x: any) => x.id === pend.id)
    if (!o) { setPend(null); pendRef.current = null; return }
    if (Math.abs(o.x - pend.x) < 1e-3 && Math.abs(o.y - pend.y) < 1e-3
        && Math.abs(o.scale - pend.scale) < 1e-3) { setPend(null); pendRef.current = null }
  }, [overlays, pend])
  useEffect(() => {
    if (selOverlay && !(overlays ?? []).some((o: any) => o.id === selOverlay)) setSelOverlay(null)
  }, [overlays, selOverlay])
  const iniciarArrasto = (e: React.MouseEvent, o: any, modo: 'move' | 'scale') => {
    e.preventDefault(); e.stopPropagation()
    const g = geo(o)
    setSelOverlay(o.id)
    setState({ selection: null, selectedClip: null })
    palco.current?.focus()
    const rc = palco.current?.getBoundingClientRect()
    ovDrag.current = { id: o.id, modo, mx: e.clientX, my: e.clientY,
                       x0: g.x, y0: g.y, s0: g.scale,
                       cx: g.left + g.width / 2, cy: g.top + g.height / 2 }
    const onMove = (ev: MouseEvent) => {
      const d = ovDrag.current
      if (!d || !rc || box.width <= 0) return
      let novo: typeof pend
      if (d.modo === 'move') {
        const nx = Math.min(1, Math.max(0, d.x0 + (ev.clientX - d.mx) / box.width))
        const ny = Math.min(1, Math.max(0, d.y0 + (ev.clientY - d.my) / box.height))
        novo = { id: d.id, x: Number(nx.toFixed(4)), y: Number(ny.toFixed(4)), scale: d.s0 }
      } else {
        // pela distância ao centro: puxar para fora cresce, para dentro encolhe
        const d0 = Math.hypot(d.mx - rc.left - d.cx, d.my - rc.top - d.cy) || 1
        const d1 = Math.hypot(ev.clientX - rc.left - d.cx, ev.clientY - rc.top - d.cy)
        const ns = Math.min(4, Math.max(0.1, d.s0 * (d1 / d0)))
        novo = { id: d.id, x: d.x0, y: d.y0, scale: Number(ns.toFixed(4)) }
      }
      pendRef.current = novo
      setPend(novo)
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      const d = ovDrag.current
      ovDrag.current = null
      const p = pendRef.current
      if (!d || !p || p.id !== d.id) return
      if (Math.abs(p.x - d.x0) > 1e-4 || Math.abs(p.y - d.y0) > 1e-4
          || Math.abs(p.scale - d.s0) > 1e-4) {
        onOverlayChange?.(d.id, { x: p.x, y: p.y, scale: p.scale })
      } else {
        pendRef.current = null
        setPend(null)
      }
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }
  const aoTeclarNoPalco = (e: React.KeyboardEvent) => {
    if (!selOverlay) return
    if (e.key === 'Delete' || e.key === 'Backspace') {
      // stopPropagation aqui é o que impede o Delete da timeline de agir junto
      e.preventDefault(); e.stopPropagation()
      onOverlayDelete?.(selOverlay); setSelOverlay(null)
    } else if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation(); setSelOverlay(null)
    }
  }

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
        // comparar TODOS os campos: deixar top de fora prendia o quadro (e a
        // legenda junto) no lugar velho quando só a altura do palco mudava
        return Math.abs(b.width - w) < 0.5 && Math.abs(b.height - h) < 0.5
          && Math.abs(b.left - novo.left) < 0.5
          && Math.abs(b.top - novo.top) < 0.5 && b.vh === vh ? b : novo
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

  // A POSIÇÃO SOBREVIVE À TROCA DE PRÉVIA. Cada retoque refaz a prévia, e a
  // prévia nova troca o `src` do <video> — que remonta e volta para 0:00. O
  // usuário apagava uma frase no minuto 6 e era devolvido ao início do vídeo
  // a cada edição. Aqui guardamos onde ele estava e se estava tocando, e o
  // elemento novo retoma dali assim que tiver metadata.
  const ultimaPosicao = useRef<{ t: number; tocando: boolean }>({ t: 0, tocando: false })
  useEffect(() => {
    ultimaPosicao.current = { t: playhead, tocando: playing }
  }, [playhead, playing])
  const retomar = useRef<{ t: number; tocando: boolean } | null>(null)
  useEffect(() => {
    // a limpeza roda ANTES do remount: é o último instante com o vídeo velho
    return () => { retomar.current = { ...ultimaPosicao.current } }
  }, [previewUrl, proxyUrl])
  const aoCarregarMetadata = useCallback(() => {
    const el = video.current
    const r = retomar.current
    retomar.current = null
    if (!el || !r || r.t <= 0.05) return
    const alvo = Math.max(0, Math.min(r.t, (el.duration || duration) - 0.05))
    if (linear) {
      el.currentTime = alvo
    } else {
      const pos = outputToSource(alvo, blocks)
      if (pos) el.currentTime = pos.time
    }
    setPlayhead(alvo)
    if (r.tocando) el.play().then(() => setPlaying(true)).catch(() => {})
  }, [linear, blocks, duration])

  // MARCAR INÍCIO / FIM DE ONDE VOCÊ ESTÁ ASSISTINDO. É o gesto que faltava:
  // "ouvi uma frase ruim" -> I no começo dela, O no fim, Delete. Sem isso o
  // retoque exigia parar, achar as palavras no texto ou o bloco na timeline,
  // selecionar e apagar — quatro ações em duas telas. A marca vai para a
  // mesma seleção que a timeline usa (eixo da FONTE), então Delete, o botão
  // "cortar" e a faixa vermelha na timeline enxergam a mesma coisa.
  const selection = useStore((st) => st.selection)
  const marcar = useCallback((qual: 'inicio' | 'fim') => {
    const pos = outputToSource(playhead, blocks)
    if (!pos) return
    const t = pos.time
    const atual = getState().selection
    if (qual === 'inicio') {
      setState({ selection: { start: t, end: Math.max(t + 0.05, atual?.end ?? t + 0.05) } })
    } else {
      setState({ selection: { start: Math.min(atual?.start ?? t - 0.05, t - 0.05), end: t } })
    }
  }, [playhead, blocks])

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
      // I = início do trecho, O = fim — o padrão de todo editor de vídeo
      if (e.key === 'i' || e.key === 'I' || e.key === '[') { e.preventDefault(); marcar('inicio') }
      if (e.key === 'o' || e.key === 'O' || e.key === ']') { e.preventDefault(); marcar('fim') }
      // X = tira a FRASE que está na tela agora. É o retoque de uma tecla:
      // você ouve a frase ruim, aperta, ela sai — sem marcar nada.
      if ((e.key === 'x' || e.key === 'X') && cueRef.current?.word_ids?.length) {
        e.preventDefault(); onCutCue?.(cueRef.current.word_ids)
      }
      if (e.code === 'ArrowLeft') seekOutput(playhead - (e.shiftKey ? 1 : 0.1))
      if (e.code === 'ArrowRight') seekOutput(playhead + (e.shiftKey ? 1 : 0.1))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const cue = cueAt(playhead, cues)
  const cueRef = useRef<SubtitleCue | null>(null)
  cueRef.current = cue ?? null
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
    // transform-origin NÃO é o centro do recorte: com scale(z) e origin o, a
    // janela visível é [o(1-1/z), o(1-1/z)+1/z]. Para o centro da janela cair
    // na âncora (que é o que o render faz), o = (a - 1/(2z)) / (1 - 1/z).
    // Sem esta conta a prévia mostrava o quadro ~5% deslocado do exportado.
    const ox = (cx - alcance) / (1 - 1 / z)
    const oy = (cy - alcance) / (1 - 1 / z)
    return {
      transform: `scale(${z})`,
      transformOrigin: `${(ox * 100).toFixed(2)}% ${(oy * 100).toFixed(2)}%`,
    } as const
  })()

  return (
    <div className="flex flex-col gap-2 h-full min-h-0">
      <div ref={palco} tabIndex={0}
           className="relative flex-1 min-h-0 bg-black rounded-lg
                      overflow-hidden border border-line focus:outline-none"
           onKeyDown={aoTeclarNoPalco}
           onMouseDown={(e) => {
             if (!(e.target as HTMLElement).closest?.('[data-ov]')) setSelOverlay(null)
           }}>
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
                 onLoadedMetadata={aoCarregarMetadata}
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
            o que vai baixar
          </span>
        )}
        {!linear && previaVelha && (
          <span className="absolute top-1.5 left-1.5 chip border-amber-800/70
                           text-amber-300 bg-ink-900/80"
                title="Você editou: a prévia final está sendo refeita. Enquanto
                       isso o player toca a cópia leve, que acompanha na hora.">
            refazendo a prévia…
          </span>
        )}
        {!linear && !previaVelha && proxyUrl && (
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
        {box.height > 0 && !stillClip && ovAtivos.map((o: any) => {
          const g = geo(o)
          const sel = o.id === selOverlay
          return (
            <div key={o.id} data-ov="1"
                 className={`absolute select-none ${sel
                   ? 'border-2 border-accent'
                   : 'border border-dashed border-white/40 hover:border-white/80'}`}
                 style={{ left: g.left, top: g.top, width: g.width, height: g.height,
                          cursor: ovDrag.current?.modo === 'move' ? 'grabbing' : 'grab' }}
                 title="arraste para mover · o canto redimensiona · Delete apaga"
                 onMouseDown={(e) => iniciarArrasto(e, o, 'move')}>
              {/* O ELEMENTO DE VERDADE dentro da caixa — a imagem, ou o vídeo
                  tocando em janela. Quando a prévia RENDERIZADA está no ar o
                  elemento já vem queimado nela e só a caixa é desenhada;
                  enquanto se arrasta, o elemento acompanha a mão. */}
              {(!linear || pend?.id === o.id) && (
                g.video
                  ? <PipVideo src={api.mediaFileUrl(projectId, o.media_id)}
                              t={(Number(o.media_start) || 0) + Math.max(0, playhead - o.out_start)}
                              playing={playing}
                              fallback={api.frameUrl(projectId,
                                (Number(o.media_start) || 0)
                                  + Math.floor(Math.max(0, playhead - o.out_start) * 2) / 2,
                                o.media_id, 540)}
                              opacity={o.opacity ?? 1} />
                  : <img src={api.mediaFileUrl(projectId, o.media_id)} alt=""
                         draggable={false}
                         className="w-full h-full object-fill pointer-events-none select-none"
                         style={{ opacity: o.opacity ?? 1 }} />
              )}
              {sel && (
                <>
                  <span className="absolute -top-5 left-0 chip border-accent/60 text-accent
                                   bg-ink-900/90 text-[10px] whitespace-nowrap">
                    {g.nome} · {Math.round(g.scale * 100)}%
                  </span>
                  <button className="absolute -top-5 right-0 chip border-red-800/70
                                     text-red-300 bg-ink-900/90 text-[10px]"
                          title="tira esta sobreposição do vídeo (Delete)"
                          onMouseDown={(e) => e.stopPropagation()}
                          onClick={(e) => {
                            e.stopPropagation()
                            onOverlayDelete?.(o.id); setSelOverlay(null)
                          }}>×</button>
                  <div data-ov="1"
                       className="absolute -right-1.5 -bottom-1.5 w-3 h-3 bg-accent
                                  rounded-sm cursor-nwse-resize"
                       onMouseDown={(e) => iniciarArrasto(e, o, 'scale')} />
                </>
              )}
            </div>
          )
        })}
        {cutAtivo && !stillClip && (
          <span className="absolute top-1.5 right-1.5 chip border-violet-700/70
                           text-violet-200 bg-ink-900/90 flex items-center gap-1">
            cobertura: {(media ?? []).find((m) => m.id === cutAtivo.media_id)?.name ?? 'vídeo'}
            <button className="text-red-300 hover:text-red-200 font-bold px-1"
                    title="tira esta cobertura do vídeo (o áudio original continua)"
                    onClick={() => onCutawayDelete?.(cutAtivo.id)}>×</button>
          </span>
        )}
      </div>

      <div className="flex items-center gap-2 text-xs">
        <button className="btn btn-xs w-16" onClick={toggle}>
          {playing ? '❚❚ pausa' : '▶ tocar'}
        </button>
        {/* o retoque em dois toques: marca onde começa, marca onde termina,
            corta. Teclas I e O fazem o mesmo sem tirar a mão do teclado. */}
        <span className="flex items-center gap-1 ml-1">
          <button className="btn btn-xs" title="marcar o INÍCIO do trecho ruim aqui (I)"
                  onClick={() => marcar('inicio')}>⟦ início</button>
          <button className="btn btn-xs" title="marcar o FIM do trecho ruim aqui (O)"
                  onClick={() => marcar('fim')}>fim ⟧</button>
          {cue?.word_ids?.length ? (
            <button className="btn btn-xs"
                    title={`tira do vídeo a frase que está na tela agora (X): “${cue.text.slice(0, 60)}”`}
                    onClick={() => onCutCue?.(cue.word_ids)}>✂ esta frase</button>
          ) : null}
          {selection && selection.end - selection.start > 0.04 && (
            <>
              <button className="btn btn-xs btn-danger"
                      title="tira este trecho do vídeo (Delete). A borda encaixa no vale e nunca come palavra."
                      onClick={() => onDeleteSelection?.()}>✂ cortar</button>
              <button className="btn btn-xs" title="desmarcar (Esc)"
                      onClick={() => setState({ selection: null })}>×</button>
            </>
          )}
        </span>
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
