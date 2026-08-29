import { api } from '../lib/api'
import { setState, useStore } from '../state/store'

const STAGE_LABEL: Record<string, string> = {
  audio: 'extraindo áudio',
  envelope: 'analisando o envelope',
  palmas: 'procurando palmas',
  ia: 'a IA decidindo os cortes',
  transcricao: 'transcrevendo',
  takes: 'aplicando a regra do take',
  cortes: 'propondo cortes',
  auditoria: 'auditando bordas',
  legendas: 'gerando legendas',
  exportando: 'exportando',
}
const ORDER = ['audio', 'envelope', 'palmas', 'transcricao', 'ia', 'takes',
  'cortes', 'auditoria', 'legendas']

export default function JobBar() {
  const job = useStore((s) => s.activeJob)
  if (!job || ['ok', 'erro', 'cancelado'].includes(job.status)) return null
  const pct = Math.round(job.progress * 100)
  const stageIndex = ORDER.indexOf(job.stage)

  return (
    <div className="border-b border-line bg-ink-700/70 px-4 py-2">
      <div className="flex items-center gap-3">
        <span className="text-xs font-medium text-accent w-28 shrink-0">
          {STAGE_LABEL[job.stage] ?? job.kind}
        </span>
        <div className="flex-1 h-1.5 bg-ink-500 rounded-full overflow-hidden">
          <div className="h-full bg-accent transition-all duration-200"
               style={{ width: `${pct}%` }} />
        </div>
        <span className="text-xs font-mono text-slate-400 w-10 text-right">{pct}%</span>
        <button className="btn btn-xs btn-danger"
                onClick={async () => {
                  await api.cancelJob(job.id)
                  setState({ activeJob: null })
                }}>
          cancelar
        </button>
      </div>
      <div className="flex items-center gap-2 mt-1.5">
        <div className="flex items-center gap-1">
          {ORDER.map((s, i) => (
            <span key={s}
                  className={`h-1 w-6 rounded-full ${
                    stageIndex >= i ? 'bg-accent/70' : 'bg-ink-500'}`}
                  title={STAGE_LABEL[s]} />
          ))}
        </div>
        <span className="text-[11px] text-slate-400 truncate">{job.message}</span>
      </div>
    </div>
  )
}
