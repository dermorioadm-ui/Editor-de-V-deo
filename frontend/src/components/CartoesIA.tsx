import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { toast, useStore } from '../state/store'

/**
 * CARTÕES A PEDIDO. A IA não inventa mais painel nenhum por conta própria:
 * cartão (hook de abertura, tópicos, número em destaque) só existe quando
 * você escreve aqui o que quer. Ela lê a sua fala, escreve só isso, e o
 * programa desenha e põe em cima do trecho certo. Os pedidos se somam;
 * apagar é um clique na prévia, ou "limpar cartões".
 */
export default function CartoesIA({ projectId, cartoes, onChanged, snapshot }: {
  projectId: string
  cartoes: { id: string; name: string }[]
  onChanged: () => Promise<any>; snapshot: () => void
}) {
  const [pedido, setPedido] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useStore((s) => (jobId ? s.jobs[jobId] : undefined))

  useEffect(() => {
    if (!job || !jobId) return
    if (job.status === 'ok') {
      setJobId(null)
      onChanged().then(() => {
        const r = job.result ?? {}
        const n = (r.cartoes ?? []).length
        toast(n ? 'ok' : 'warn', n ? `${n} cartão(ões) no vídeo` : 'Nenhum cartão',
          [r.leitura, ...(r.recusados ?? []).map((x: any) => `${x.o_que}: ${x.motivo}`)]
            .filter(Boolean).join(' · ').slice(0, 300))
        if (n) setPedido('')
      })
    } else if (job.status === 'erro' || job.status === 'cancelado') {
      setJobId(null)
      toast('error', 'Os cartões não saíram', job.error || job.message)
    }
  }, [job?.status])   // eslint-disable-line react-hooks/exhaustive-deps

  const pedir = async () => {
    if (!pedido.trim()) { toast('warn', 'Escreva o que você quer no cartão'); return }
    snapshot()
    try {
      const j = await api.cartoesIa(projectId, pedido.trim())
      setJobId(j.id)
    } catch (e: any) {
      toast('error', 'Não deu para pedir à IA', String(e.message ?? e))
    }
  }
  const ocupado = !!jobId && !!job && ['fila', 'rodando'].includes(job.status)

  return (
    <section className="card p-3" data-cartoes-ia="1">
      <div className="flex items-center gap-2 mb-1">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
          Cartões e hooks por pedido
        </h3>
        <span className="text-[10px] text-slate-500">
          a IA só escreve o que você pedir aqui — nada por conta própria
        </span>
        {cartoes.length > 0 && (
          <button className="btn btn-xs ml-auto" title="tira todos os cartões do vídeo"
                  onClick={async () => {
                    snapshot()
                    await api.limparCartoes(projectId)
                    await onChanged()
                    toast('ok', 'Cartões removidos')
                  }}>limpar cartões ({cartoes.length})</button>
        )}
      </div>
      <textarea className="field w-full text-xs" rows={2}
                placeholder={'ex.: um hook de abertura com a promessa do vídeo · os três passos em cartão de tópicos · o número de clientes em destaque'}
                value={pedido} onChange={(e) => setPedido(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) pedir() }} />
      <div className="flex items-center gap-2 mt-2">
        <button className="btn btn-xs btn-primary" disabled={ocupado} onClick={pedir}>
          {ocupado ? (job?.message || 'escrevendo…') : 'criar os cartões'}
        </button>
        <span className="text-[10px] text-slate-500">
          entram em cima da fala certa; arraste, redimensione ou apague na prévia
        </span>
      </div>
      {cartoes.length > 0 && (
        <p className="text-[10px] text-slate-500 mt-1.5 truncate">
          no vídeo: {cartoes.map((c) => c.name.replace(/^cartão: /, '')).join(' · ')}
        </p>
      )}
    </section>
  )
}
