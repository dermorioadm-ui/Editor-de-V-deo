import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { setPlayhead, setState, toast, useStore } from '../state/store'
import { timecode } from '../lib/format'

export default function DiretorPanel() {
  const project = useStore(s => s.project)
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
  // ocupado SÓ com direção de verdade: a prévia que se refaz sozinha, o logo
  // 3D renderizando e a exportação também são "job ativo", e deixavam o botão
  // travado em "Diretor trabalhando…" — não havia onde disparar a correção
  const dirigindo = useStore(s => !!project && (Object.values(s.jobs ?? {}) as any[]).some(
    (j: any) => j.project_id === project.id && ['diretor', 'claude', 'clique-unico'].includes(j.kind)
      && ['fila', 'rodando'].includes(j.status)))
  const rodando = enviando || dirigindo
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
    <label className="block text-xs">O que corrigir / orientação para o diretor
      <textarea className="field w-full" rows={3} value={pedido} onChange={e => setPedido(e.target.value)}
        data-campo="correcao"
        placeholder="Ex.: a lista entrou atrasada; troque o ícone do minuto 0:42 por um objeto 3D de casa; ponha transição na virada do 1:10." />
    </label>
    <button className="btn btn-primary w-full" disabled={rodando} onClick={pedir} data-disparar-direcao="1">
      {rodando ? 'Diretor trabalhando…' : pedido.trim() ? 'Corrigir agora' : 'Dirigir esta etapa'}</button>
    <p className="text-[11px] text-slate-500">A IA mexe no que você pediu e, no fim, a prévia e o vídeo final são refeitos sozinhos.</p>
    {relatorio?.erro && <p className="text-xs text-amber-300" role="alert">{relatorio.erro}</p>}
    {relatorio?.aviso && !relatorio?.erro && <p className="text-xs text-slate-400">{relatorio.aviso}</p>}
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
