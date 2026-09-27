import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { setPlayhead, setState, toast, useStore } from '../state/store'
import { timecode } from '../lib/format'

export default function DiretorPanel() {
  const project = useStore(s => s.project)
  const job = useStore(s => s.activeJob)
  const [provedor, setProvedor] = useState('codex')
  const [modo, setModo] = useState('pos')
  const [pedido, setPedido] = useState('')
  const [plano, setPlano] = useState<any>(null)
  const [enviando, setEnviando] = useState(false)
  const [aberto, setAberto] = useState(false)
  useEffect(() => {
    if (!project) return
    setProvedor((project.plan as any)?.pos_editor || project.plan?.editor || 'codex')
  }, [project?.id])
  useEffect(() => {
    if (!project) return
    let vivo = true
    api.direcao(project.id).then(r => { if (vivo) setPlano(r) }).catch(() => {})
    return () => { vivo = false }
  }, [project?.id, project?.analysis, project?.plan])
  if (!project) return null
  const rodando = enviando || (job?.project_id === project.id && ['fila', 'rodando'].includes(job.status))
  const relatorio = project.analysis?.diretor_edicao
  async function pedir() {
    if (!project) return
    setEnviando(true)
    try {
      const r = await api.diretorPedir(project.id, provedor, modo, pedido)
      setState({ activeJob: r }); setPedido('')
    } catch (e: any) { toast('warn', 'Não deu para iniciar a direção', e.message) }
    finally { setEnviando(false) }
  }
  return <section className="card p-3 space-y-3" data-diretor-panel="1">
    <div className="flex items-center justify-between gap-2 flex-wrap">
      <h3 className="text-sm font-medium text-slate-100">Direção criativa</h3>
      <span className={`text-xs ${plano?.aprovada ? 'text-emerald-300' : 'text-amber-300'}`}>
        {plano?.aprovada ? 'Quadros conferidos' : plano?.momentos?.length ? 'Conferência pendente' : 'Plano ainda não criado'}
      </span>
    </div>
    <div className="flex gap-2 flex-wrap">
      <label className="text-xs flex-1">Diretor
        <select className="field w-full" value={provedor} onChange={e => setProvedor(e.target.value)} disabled={rodando}>
          <option value="codex">Codex · ChatGPT</option><option value="claude">Claude</option>
        </select>
      </label>
      <label className="text-xs flex-1">Etapa
        <select className="field w-full" value={modo} onChange={e => setModo(e.target.value)} disabled={rodando}>
          <option value="pos">Acabamento</option><option value="edicao">Revisão dos cortes</option><option value="completo">Edição completa</option>
        </select>
      </label>
    </div>
    <label className="block text-xs">Orientação para o diretor
      <textarea className="field w-full" rows={2} value={pedido} onChange={e => setPedido(e.target.value)}
        placeholder="Ex.: preserve as pausas de impacto, explique o mecanismo em etapas e dê destaque à prova." />
    </label>
    <button className="btn btn-primary w-full" disabled={rodando} onClick={pedir}>
      {rodando ? 'Diretor trabalhando…' : 'Dirigir esta etapa'}</button>
    {relatorio?.erro && <p className="text-xs text-amber-300" role="alert">{relatorio.erro}</p>}
    {(plano?.momentos?.length > 0 || relatorio?.relatorio) && <>
      <button className="btn btn-xs" aria-expanded={aberto} onClick={() => setAberto(!aberto)}>Plano e decisões</button>
      {aberto && <div className="space-y-2 text-xs">
        {plano?.objetivo && <p className="text-slate-200">{plano.objetivo}</p>}
        {plano?.linguagem && <p className="text-slate-400">{plano.linguagem}</p>}
        <ol className="space-y-2">{(plano?.momentos || []).map((m: any, i: number) =>
          <li key={i}><button className="btn btn-xs mr-2" onClick={() => setPlayhead(m.inicio)}>{timecode(m.inicio)}</button>
            <span className="text-slate-200">{m.intencao}</span></li>)}</ol>
        {relatorio?.relatorio && <p className="whitespace-pre-wrap text-slate-300">{relatorio.relatorio}</p>}
        <p className="text-slate-500">A conferência registrada cobre quadros estáticos. Assista à prévia para avaliar movimento e áudio.</p>
      </div>}
    </>}
  </section>
}
