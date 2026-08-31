import { useEffect } from 'react'
import { api } from '../lib/api'
import { setState, useStore } from '../state/store'

/** A tela entre soltar o arquivo e receber o vídeo PRONTO.
 *
 *  O editor não aparece pela metade: enquanto o pipeline roda — transcrição,
 *  IA decidindo os cortes, legendas, jogo de câmeras, prévia — o usuário vê
 *  ONDE o processo está, não uma interface vazia cheia de botões que ainda
 *  não fazem nada. Editar começa quando há o que editar. */

// os ~14 estágios internos, agrupados em 5 passos que uma pessoa entende.
// A EXPORTAÇÃO não está aqui de propósito: ela roda por baixo depois que o
// editor abre, porque é o passo mais caro de todos e segurar esta tela até
// ela acabar é ficar minutos olhando barra com o vídeo já montado.
const PASSOS: { rotulo: string; stages: string[] }[] = [
  { rotulo: 'Ouvindo o áudio', stages: ['audio', 'envelope', 'palmas'] },
  { rotulo: 'Transcrevendo o que você falou',
    stages: ['transcricao', 'encaixe', 'comandos', 'assobio', 'rosto'] },
  { rotulo: 'Cortando (IA + marcadores)',
    stages: ['ia', 'takes', 'repeticao', 'cortes', 'auditoria'] },
  { rotulo: 'Jogo de câmeras e legendas', stages: ['zoom', 'legendas'] },
  { rotulo: 'Montando o vídeo pronto', stages: ['proxy', 'previa'] },
]

export default function ProcessingView() {
  const project = useStore((s) => s.project)
  const activeJob = useStore((s) => s.activeJob)

  const meu = activeJob && activeJob.project_id === project?.id ? activeJob : null
  const rodando = !!meu && ['fila', 'rodando'].includes(meu.status)
  // o job terminou mas o projeto ainda não foi recarregado: é "abrindo", não
  // "parado" — mostrar o botão aqui fazia o usuário re-rodar o pipeline
  // inteiro num clique de reflexo
  const abrindo = meu?.status === 'ok'
  const falhou = meu?.status === 'erro'
  const stage = meu?.stage ?? ''
  const atual = Math.max(0, PASSOS.findIndex((p) => p.stages.includes(stage)))

  // REDE DE SEGURANÇA contra o giro eterno: a fila de jobs vive em memória e
  // o WebSocket não manda o estado ao reconectar — se o evento final se
  // perder (servidor reiniciado, CPU saturada pelo ffmpeg), esta tela ficava
  // girando para sempre. A cada 3 s ela mesma confere a verdade no servidor.
  useEffect(() => {
    if (!project) return
    const t = window.setInterval(async () => {
      try {
        const jobs = await api.jobs(project.id)
        const vivo = jobs.find((j: any) =>
          ['fila', 'rodando'].includes(j.status))
        if (vivo) { setState({ activeJob: vivo }); return }
        const p = await api.project(project.id)
        if (p.analysis?.words?.length) {
          setState({
            project: p, timeline: p.timeline ?? null,
            words: p.analysis?.words ?? [],
            removedWordIds: p.analysis?.removed_word_ids ?? [],
            fillers: p.analysis?.fillers ?? [],
            activeJob: null,
          })
        }
      } catch { /* servidor fora: tenta no próximo tique */ }
    }, 3000)
    return () => window.clearInterval(t)
  }, [project?.id])

  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="w-full max-w-md">
        <p className="text-xs text-slate-500 uppercase tracking-widest mb-1">
          preparando o seu vídeo
        </p>
        <h2 className="text-lg font-semibold text-slate-100 truncate mb-6">
          {project?.name}
        </h2>

        <ol className="space-y-3">
          {PASSOS.map((p, i) => {
            const feito = rodando && i < atual
            const agora = rodando && i === atual
            return (
              <li key={p.rotulo} className="flex items-center gap-3">
                <span className={`w-6 h-6 shrink-0 rounded-full grid place-items-center
                  text-[11px] font-bold transition-colors
                  ${feito ? 'bg-emerald-500 text-ink-900'
                    : agora ? 'bg-accent text-ink-900'
                    : 'bg-ink-700 text-slate-500'}`}>
                  {feito ? (
                    <svg viewBox="0 0 16 16" className="w-3.5 h-3.5" fill="none"
                         stroke="currentColor" strokeWidth="2.5">
                      <path d="M3 8.5 6.5 12 13 4.5" strokeLinecap="round" />
                    </svg>
                  ) : agora ? (
                    <svg viewBox="0 0 16 16"
                         className="w-3.5 h-3.5 animate-spin" fill="none"
                         stroke="currentColor" strokeWidth="2.5">
                      <path d="M8 2a6 6 0 1 1-6 6" strokeLinecap="round" />
                    </svg>
                  ) : i + 1}
                </span>
                <span className={`text-sm transition-colors
                  ${agora ? 'text-slate-100 font-medium'
                    : feito ? 'text-slate-400' : 'text-slate-600'}`}>
                  {p.rotulo}
                </span>
              </li>
            )
          })}
        </ol>

        <div className="mt-6 h-1.5 rounded-full bg-ink-700 overflow-hidden">
          <div className="h-full bg-accent rounded-full transition-[width] duration-500"
               style={{ width: `${Math.round((meu?.progress ?? 0) * 100)}%` }} />
        </div>
        {meu?.message && (
          <p className="text-[11px] text-slate-500 mt-2 truncate">
            {meu.message}
          </p>
        )}

        {falhou && (
          <div className="mt-6 card border-red-900/60 bg-red-950/20 p-3">
            <p className="text-sm text-red-300 font-medium">Não deu</p>
            <p className="text-[12px] text-slate-400 mt-1">{activeJob?.error}</p>
            <button className="btn btn-xs mt-2"
                    onClick={async () => {
                      const job = await api.oneclick(project!.id,
                        project!.preset ?? 'VSL')
                      setState({ activeJob: job })
                    }}>
              tentar de novo
            </button>
          </div>
        )}
        {abrindo && (
          <p className="text-sm text-slate-400 mt-6 text-center">
            abrindo o editor…
          </p>
        )}
        {!rodando && !falhou && !abrindo && (
          <button className="btn btn-primary mt-6 w-full"
                  onClick={async () => {
                    const job = await api.oneclick(project!.id,
                      project!.preset ?? 'VSL')
                    setState({ activeJob: job })
                  }}>
            Editar este vídeo
          </button>
        )}

        <p className="text-[11px] text-slate-600 mt-8 leading-relaxed">
          Na próxima gravação: diga <b className="text-slate-400">"corta"</b> quando
          errar — a tentativa sai sozinha. E{' '}
          <b className="text-slate-400">"próximo"</b> depois de uma frase que
          você quer <b>intocável</b>: nem a IA mexe no que vem antes dela. As
          palavras de comando não aparecem no vídeo.
        </p>
      </div>
    </div>
  )
}
