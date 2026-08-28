import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { getPlayhead, toast, useStore } from '../state/store'

interface Props { onChanged: () => Promise<any>; snapshot: () => void }

/**
 * Filtros de cinema. O quadro de cada opção é gerado com o filtro APLICADO —
 * o usuário escolhe olhando o próprio vídeo dele, não um nome.
 */
export default function LookPanel({ onChanged, snapshot }: Props) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const [looks, setLooks] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [t, setT] = useState(0)

  useEffect(() => { api.looks().then(setLooks).catch(() => {}) }, [])
  useEffect(() => { setT(getPlayhead()) }, [project?.id])

  if (!project || !view) return null
  const atual = view.look ?? 'nenhum'
  const escolhido = looks.find((l) => l.id === atual)
  const vinheta = view.look_vignette ?? escolhido?.vignette ?? 0

  return (
    <div className="p-4 max-w-5xl space-y-4">
      <section className="card p-3">
        <div className="flex items-center gap-2 mb-1">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Filtro de cinema
          </h3>
          <button className="btn btn-xs ml-auto"
                  onClick={() => setT(getPlayhead())}>
            usar o quadro de agora
          </button>
        </div>
        <p className="hint mb-3">
          Vale para o vídeo inteiro e entra no <b>mesmo encode</b> — não custa
          geração nenhuma e a legenda não é afetada. As miniaturas abaixo são o
          seu vídeo com o filtro já aplicado.
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {looks.map((l) => {
            const sel = l.id === atual
            return (
              <button key={l.id}
                      disabled={busy}
                      onClick={async () => {
                        snapshot()
                        setBusy(true)
                        try {
                          await api.setLook(project.id, l.id, null)
                          await onChanged()
                          toast('ok', `Filtro: ${l.label}`, l.description)
                        } finally { setBusy(false) }
                      }}
                      className={`text-left rounded-md overflow-hidden border transition
                                  ${sel ? 'border-accent ring-1 ring-accent/60'
                                        : 'border-line hover:border-slate-600'}`}>
                <div className="aspect-[9/16] bg-black max-h-44 overflow-hidden">
                  <img className="w-full h-full object-cover"
                       src={api.frameUrl(project.id, t, 'main', 300,
                                         l.id === 'nenhum' ? '' : l.id)}
                       alt={l.label} loading="lazy" />
                </div>
                <div className="p-1.5">
                  <div className="text-[11px] font-medium text-slate-200">
                    {l.label}{sel && ' ✓'}
                  </div>
                  <div className="text-[10px] text-slate-500 leading-snug">
                    {l.description}
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      </section>

      <section className="card p-3">
        <label className="label">
          Vinheta · <span className="font-mono text-slate-300">
            {Math.round(vinheta * 100)}%</span>
        </label>
        <p className="hint mb-1.5">
          Escurece os cantos e segura o olho no rosto. Cada filtro já vem com a
          dele; aqui você força outro valor.
        </p>
        <input type="range" min={0} max={1} step={0.05} value={vinheta}
               className="w-full" disabled={busy}
               onChange={async (e) => {
                 setBusy(true)
                 try {
                   await api.setLook(project.id, atual, Number(e.target.value))
                   await onChanged()
                 } finally { setBusy(false) }
               }} />
        <button className="btn btn-xs mt-2"
                onClick={async () => {
                  await api.setLook(project.id, atual, null)
                  await onChanged()
                }}>
          voltar para a vinheta do filtro
        </button>
      </section>
    </div>
  )
}
