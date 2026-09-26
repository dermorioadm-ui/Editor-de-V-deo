import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { toast, useStore } from '../state/store'

const NOME_FONTE: Record<string, string> = {
  pexels: 'Pexels', pixabay: 'Pixabay', biblioteca: 'sua biblioteca',
}

const FREQ: Record<string, string> = {
  pouco: 'pouco (1 a cada ~20 s)', medio: 'médio (1 a cada ~12 s)', muito: 'muito (1 a cada ~7 s)',
}

/**
 * B-ROLL AUTOMÁTICO no vídeo montado: a IA (ou a regra do programa, sem a
 * chave do Gemini) escolhe onde entra, quanto dura e o que mostrar; o vídeo
 * sai da biblioteca dele ou do banco grátis. Refazer troca só os
 * automáticos — os postos à mão ficam. Cada um se ajusta clicando nele no
 * trilho B-roll.
 */
export default function BrollAutomatico({ projectId, onChanged, snapshot }: {
  projectId: string; onChanged: () => Promise<any>; snapshot: () => void
}) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const [freq, setFreq] = useState<string>(project?.plan?.broll?.frequencia ?? 'medio')
  // de onde vem o vídeo: o banco grátis (Pexels e Pixabay) primeiro, ou a
  // biblioteca dele primeiro
  const [fonte, setFonte] = useState<string>(project?.plan?.broll?.fonte ?? 'banco')
  const [estado, setEstado] = useState<any>(null)
  const [ia, setIa] = useState<any>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useStore((s) => (jobId ? s.jobs[jobId] : undefined))

  useEffect(() => {
    api.bancoEstado().then((e) => {
      setEstado(e)
      const falta = ['pexels', 'pixabay'].some((f) => e?.[f]?.tem_chave && e?.[f]?.funciona == null)
      if (falta) api.bancoTestar().then(setEstado).catch(() => {})
    }).catch(() => setEstado(null))
    api.aiConfig().then(setIa).catch(() => setIa(null))
  }, [projectId])

  useEffect(() => {
    if (!job || !jobId) return
    if (job.status === 'ok') {
      setJobId(null)
      onChanged().then(() => {
        const r = job.result ?? {}
        const n = r.postos?.length ?? 0
        const de = Object.entries(r.por_fonte ?? {})
          .map(([k, v]) => `${v} do ${NOME_FONTE[k] ?? k}`).join(', ')
        toast(n ? 'ok' : 'warn', n ? `${n} b-roll(s) no vídeo${de ? ` — ${de}` : ''}` : 'Nenhum b-roll entrou',
          (r.quem === 'ia' ? 'A IA escolheu os pontos. ' : 'A regra do programa escolheu os pontos. ')
          + (r.pulados?.length ? `${r.pulados.length} ponto(s) sem vídeo: ${r.pulados[0].motivo}. ` : '')
          + (n ? 'Clique num b-roll no trilho para ajustar ou substituir.' : ''))
      })
    } else if (job.status === 'erro' || job.status === 'cancelado') {
      setJobId(null)
      toast('error', 'O b-roll automático falhou', job.error || job.message)
    }
  }, [job?.status])   // eslint-disable-line react-hooks/exhaustive-deps

  if (!project) return null
  const autos = (view?.cutaways ?? []).filter((c: any) => c.origem === 'auto')
  const ultima = project.plan?.broll?.ultima
  const ocupado = !!jobId && !!job && ['fila', 'rodando'].includes(job.status)
  const semBanco = estado && !estado.alguma
  const bibliotecaVazia = estado && !estado.baixados

  return (
    <section className="card p-3" data-broll-auto="1">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
          B-roll automático</h3>
        <select className="field text-xs py-0.5 w-48" value={freq}
                onChange={(e) => setFreq(e.target.value)}>
          {Object.entries(FREQ).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select className="field text-xs py-0.5 w-56" value={fonte} data-fonte="1"
                onChange={(e) => setFonte(e.target.value)}>
          <option value="banco">vídeos do Pexels e Pixabay (grátis)</option>
          <option value="biblioteca">minha biblioteca primeiro</option>
        </select>
        <button className="btn btn-xs btn-primary" disabled={ocupado} data-por-sozinho="1"
                onClick={async () => {
                  snapshot()
                  try {
                    const j = await api.brollAuto(projectId, freq, fonte)
                    setJobId(j.id)
                  } catch (e: any) {
                    toast('error', 'Não deu para começar', String(e.message ?? e))
                  }
                }}>
          {ocupado ? (job?.message || 'escolhendo…')
            : autos.length ? 'refazer os automáticos' : 'pôr b-roll sozinho'}
        </button>
        {autos.length > 0 && (
          <button className="btn btn-xs" disabled={ocupado}
                  onClick={async () => {
                    snapshot()
                    await api.brollAutoTirar(projectId)
                    await onChanged()
                    toast('ok', 'B-rolls automáticos tirados', 'Os que você pôs à mão ficaram.')
                  }}>tirar os automáticos ({autos.length})</button>
        )}
      </div>
      <p className="hint mt-1.5">
        {ia?.tem_chave
          ? 'A IA lê a fala e escolhe onde dá para ilustrar, quanto dura e o que mostrar. '
          : 'Sem a chave do Gemini, a regra do programa escolhe: uma frase a cada tanto, pelas palavras dela. '}
        O vídeo vem da sua biblioteca (pelas palavras-chave) ou do banco grátis. A fala continua
        por baixo; o começo e o fim ficam com o seu rosto.
      </p>
      {estado && (
        <p className="text-[10px] mt-1" data-chaves-estado="1">
          {(['pexels', 'pixabay'] as const).map((f, k) => {
            const e = estado[f] ?? {}
            const nome = NOME_FONTE[f]
            const txt = !e.tem_chave ? `${nome}: sem chave (cole aqui embaixo)`
              : e.funciona === true ? `${nome} ✓ funcionando`
                : e.funciona === false ? `${nome} ✗ ${e.aviso}` : `${nome}: testando…`
            const cor = !e.tem_chave ? 'text-slate-500'
              : e.funciona === false ? 'text-red-300' : 'text-emerald-300'
            return <span key={f} className={cor}>{k ? ' · ' : ''}{txt}</span>
          })}
        </p>
      )}
      {semBanco && bibliotecaVazia && (
        <p className="text-[11px] text-amber-300 mt-1">
          Falta de onde tirar os vídeos: cole a chave grátis do banco (aqui embaixo) ou envie
          vídeos seus para a biblioteca.</p>
      )}
      {ultima && (
        <p className="text-[10px] text-slate-500 mt-1">
          última vez: {ultima.postos} posto(s)
          {Object.keys(ultima.por_fonte ?? {}).length > 0 && ` (${Object.entries(ultima.por_fonte)
            .map(([k, v]) => `${v} do ${NOME_FONTE[k] ?? k}`).join(', ')})`}
          {ultima.pulados ? `, ${ultima.pulados} sem vídeo` : ''}
          {' '}· {ultima.quem === 'ia' ? 'escolhidos pela IA' : 'escolhidos pela regra'}
          {ultima.aviso ? ` · ${ultima.aviso}` : ''}
        </p>
      )}
      {autos.length > 0 && (
        <p className="text-[10px] text-slate-400 mt-1">
          {autos.map((c: any) => `${timecode(c.out_start)} “${c.termo || 'b-roll'}”`).join(' · ')}
        </p>
      )}
    </section>
  )
}
