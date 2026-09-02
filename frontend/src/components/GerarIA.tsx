import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { getPlayhead, toast, useStore } from '../state/store'

/**
 * GERAR COM IA, de dentro da edição: uma imagem (Nano Banana) ou um vídeo
 * (Veo) a partir do que você escreve. O resultado vira mídia do projeto e
 * entra como JANELA em cima do vídeo no ponto em que o cursor está — a fala
 * principal continua por baixo, o vídeo gerado entra sem o áudio dele. Na
 * prévia você arrasta, encolhe ou apaga como qualquer outro elemento.
 */
export default function GerarIA({ projectId, horizontal, onChanged, snapshot }: {
  projectId: string; horizontal: boolean
  onChanged: () => Promise<any>; snapshot: () => void
}) {
  const [tipo, setTipo] = useState<'image' | 'video'>('image')
  const [prompt, setPrompt] = useState('')
  const [proporcao, setProporcao] = useState(horizontal ? '16:9' : '9:16')
  const [duracao, setDuracao] = useState(4)
  const [jobId, setJobId] = useState<string | null>(null)
  const [ia, setIa] = useState<any>(null)
  const job = useStore((s) => (jobId ? s.jobs[jobId] : undefined))

  useEffect(() => {
    api.aiConfig().then(setIa).catch(() => setIa(null))
  }, [])
  useEffect(() => {
    if (!job || !jobId) return
    if (job.status === 'ok') {
      setJobId(null)
      onChanged().then(() => {
        const r = job.result ?? {}
        toast('ok', tipo === 'video' ? 'Vídeo gerado' : 'Imagem gerada',
          r.overlay
            ? `Entrou como janela em ${timecode(r.overlay.out_start)}. ` +
              'Arraste, redimensione ou apague em cima da prévia.'
            : (r.aviso ?? 'Está na mídia do projeto.'))
      })
    } else if (job.status === 'erro' || job.status === 'cancelado') {
      setJobId(null)
      toast('error', 'A geração falhou', job.error || job.message)
    }
  }, [job?.status])   // eslint-disable-line react-hooks/exhaustive-deps

  const gerar = async () => {
    if (!prompt.trim()) { toast('warn', 'Descreva o que gerar'); return }
    snapshot()
    try {
      const j = await api.gerarIa(projectId, {
        tipo, prompt: prompt.trim(), proporcao,
        out_start: getPlayhead(), duracao: tipo === 'image' ? duracao : 0,
        duracao_video: 8, colocar: true,
      })
      setJobId(j.id)
    } catch (e: any) {
      toast('error', 'Não deu para pedir à IA', String(e.message ?? e))
    }
  }

  const ocupado = !!jobId && !!job && ['fila', 'rodando'].includes(job.status)
  return (
    <section className="card p-3" data-gerar-ia="1">
      <div className="flex items-center gap-2 mb-2">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
          Gerar com IA
        </h3>
        <span className="text-[10px] text-slate-500">
          entra como janela em {timecode(getPlayhead())} — a fala continua por baixo
        </span>
      </div>
      {ia && !ia.tem_chave && (
        <p className="text-[11px] text-amber-400/90 mb-2">
          Falta a chave do Gemini: cole na tela inicial (Ajustes › IA), uma vez só.
        </p>
      )}
      <div className="flex flex-wrap gap-2 items-start">
        <div className="flex gap-1">
          <button className={`btn btn-xs ${tipo === 'image' ? 'btn-primary' : ''}`}
                  onClick={() => setTipo('image')}>imagem</button>
          <button className={`btn btn-xs ${tipo === 'video' ? 'btn-primary' : ''}`}
                  onClick={() => setTipo('video')}>vídeo (Veo)</button>
        </div>
        <select className="field w-24" value={proporcao}
                onChange={(e) => setProporcao(e.target.value)}>
          <option value="16:9">16:9</option>
          <option value="9:16">9:16</option>
          {tipo === 'image' && <option value="1:1">1:1</option>}
        </select>
        {tipo === 'image' && (
          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            <input className="field w-16" type="number" min={1} max={30} step={0.5}
                   value={duracao} onChange={(e) => setDuracao(+e.target.value || 4)} />
            s na tela
          </label>
        )}
      </div>
      <textarea className="field w-full mt-2 text-xs" rows={2}
                placeholder={tipo === 'video'
                  ? 'ex.: mãos digitando num notebook, luz de escritório, câmera lenta'
                  : 'ex.: gráfico de barras subindo, fundo escuro, estilo clean'}
                value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      <div className="flex items-center gap-2 mt-2">
        <button className="btn btn-xs btn-primary" disabled={ocupado} onClick={gerar}>
          {ocupado ? (job?.message || 'gerando…') : 'gerar e pôr no vídeo'}
        </button>
        {tipo === 'video' && (
          <span className="text-[10px] text-slate-500">
            o Veo leva alguns minutos e exige faturamento na conta Google
          </span>
        )}
      </div>
    </section>
  )
}
