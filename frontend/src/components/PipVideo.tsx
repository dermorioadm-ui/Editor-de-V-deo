import { useEffect, useRef, useState } from 'react'

/**
 * Um vídeo tocando em JANELA por cima da prévia (picture-in-picture), mudo.
 *
 * Acompanha o cursor: parado, mostra o quadro exato; tocando, toca junto e
 * só reencaixa quando a deriva passa de um terço de segundo (reencaixar a
 * cada tique faria o vídeo gaguejar). O áudio dele nunca toca — na
 * exportação ele também não entra: a fala principal continua por baixo.
 * Formato que o navegador não abre (MKV, MOV com HEVC) cai num quadro
 * extraído pelo servidor.
 */
export default function PipVideo({ src, t, playing, fallback, opacity = 1 }: {
  src: string; t: number; playing: boolean; fallback: string; opacity?: number
}) {
  const ref = useRef<HTMLVideoElement>(null)
  const [erro, setErro] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || erro) return
    const alvo = Math.max(0, t)
    if (playing) {
      if (Math.abs(el.currentTime - alvo) > 0.35) el.currentTime = alvo
      if (el.paused) el.play().catch(() => { /* autoplay negado: fica no quadro */ })
    } else {
      if (!el.paused) el.pause()
      if (Math.abs(el.currentTime - alvo) > 0.04) el.currentTime = alvo
    }
  }, [t, playing, erro])
  if (erro) {
    return <img src={fallback} alt="" draggable={false} style={{ opacity }}
                className="w-full h-full object-fill pointer-events-none select-none" />
  }
  return (
    <video ref={ref} src={src} muted playsInline preload="auto" style={{ opacity }}
           className="w-full h-full object-fill pointer-events-none select-none"
           onError={() => setErro(true)} />
  )
}
