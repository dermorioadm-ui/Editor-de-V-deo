import { useEffect, useRef } from 'react'

/**
 * A TRILHA TOCANDO NA PRÉVIA AO VIVO.
 *
 * A música entra no arquivo (build_audio_track mixa com ducking por
 * sidechain), mas a prévia ao vivo toca só o áudio da gravação: quem mexia no
 * volume não ouvia nada mudar e só descobria o resultado quando o MP4 ficava
 * pronto. Aqui um segundo elemento de áudio segue o cursor e aplica a MESMA
 * conta do render — ganho em dB, fade de entrada e saída, a curva de nível
 * que a IA escreveu, e o abaixamento na fala.
 *
 * O ducking é aproximação, não igualdade: no arquivo ele é sidechain
 * disparado pela voz; aqui é a legenda corrente que diz "tem alguém falando".
 * O arquivo continua sendo a verdade — isto é para o ouvido decidir o volume.
 */
export default function TrilhaPreview({ src, music, playhead, playing, duracao,
                                        falando, mudoGeral }: {
  src: string
  music: any
  playhead: number
  playing: boolean
  duracao: number
  /** tem fala agora? (a legenda corrente) — é o gatilho do ducking */
  falando: boolean
  /** o botão "som off" do player cala tudo, trilha inclusive */
  mudoGeral: boolean
}) {
  const ref = useRef<HTMLAudioElement>(null)
  const inicio = Math.max(0, Number(music?.out_start) || 0)
  const fim = music?.out_end ? Math.min(Number(music.out_end), duracao) : duracao
  const dur = Math.max(0.1, fim - inicio)
  const dentro = playhead >= inicio - 0.02 && playhead <= fim + 0.02
  const tRel = Math.max(0, Math.min(dur, playhead - inicio))

  // ------------------------------------------------------------ o volume
  const dB = (v: number) => (v <= -40 ? 0 : Math.pow(10, v / 20))
  let vol = dB(Number(music?.gain_db ?? -18))
  const fadeIn = Math.max(0, Number(music?.fade_in ?? 1))
  const fadeOut = Math.max(0, Number(music?.fade_out ?? 2))
  if (fadeIn > 0 && tRel < fadeIn) vol *= tRel / fadeIn
  if (fadeOut > 0 && tRel > dur - fadeOut) vol *= Math.max(0, (dur - tRel) / fadeOut)
  // a curva que a IA escreveu vem em tempo de SAÍDA
  for (const faixa of (music?.curva ?? [])) {
    const a = Number(faixa?.inicio ?? 0); const b = Number(faixa?.fim ?? 0)
    if (playhead >= a && playhead <= b) { vol *= dB(Number(faixa?.db ?? 0)); break }
  }
  if (music?.ducking !== false && falando) {
    vol *= dB(-Math.abs(Number(music?.duck_amount ?? 12)))
  }
  if (mudoGeral || music?.muted || music?.enabled === false || !dentro) vol = 0
  const alvo = Math.max(0, Math.min(1, vol))

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.volume = alvo
  }, [alvo])

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (!dentro || !playing || alvo <= 0.0005) {
      if (!el.paused) el.pause()
      // parado, o cursor da trilha acompanha o do vídeo para o play seguinte
      if (!playing && Math.abs(el.currentTime - tRel) > 0.06) el.currentTime = tRel
      return
    }
    if (Math.abs(el.currentTime - tRel) > 0.35) el.currentTime = tRel
    if (el.paused) el.play().catch(() => { /* sem gesto ainda: entra no próximo play */ })
  }, [dentro, playing, tRel, alvo])

  if (!src || !music?.media_id) return null
  return <audio ref={ref} src={src} preload="auto" className="hidden" />
}
