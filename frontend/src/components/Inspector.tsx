import { useMemo, useState } from 'react'
import { SECTIONS } from '../types'
import { api } from '../lib/api'
import { seconds, timecode } from '../lib/format'
import { setState, toast, useStore } from '../state/store'

interface Props { onChanged: () => Promise<any>; snapshot: () => void }

export default function Inspector({ onChanged, snapshot }: Props) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const selectedId = useStore((s) => s.selectedClip)
  const playhead = useStore((s) => s.playhead)
  const [globalSpeed, setGlobalSpeed] = useState(1)
  const [busy, setBusy] = useState(false)

  const block = useMemo(
    () => view?.blocks.find((b) => b.id === selectedId) ?? null, [view, selectedId])
  const warnAbove = project?.plan?.speed?.warn_above ?? 1.25

  if (!project || !view) return null

  const setSpeed = async (value: number) => {
    if (!block) return
    snapshot()
    setBusy(true)
    try {
      const res = await api.setSpeed(project.id, block.id, value)
      await onChanged()
      if (res.warn) toast('warn', `${value.toFixed(2)}x`, res.warn_message)
      if (res.boundaries_moved?.length) {
        toast('info', 'Fronteira de velocidade encaixada no silêncio',
          res.boundaries_moved.map((m: any) =>
            `${m.from.toFixed(3)} s → ${m.to.toFixed(3)} s`).join('\n'))
      }
    } finally { setBusy(false) }
  }

  return (
    <div className="p-3 space-y-4">
      {/* ------------------------------------------------ alertas de borda */}
      {view.audit?.length > 0 && (
        <section className="card border-red-900/60 bg-red-950/25 p-3">
          <h3 className="text-xs font-semibold text-red-300 uppercase tracking-wide mb-2">
            Auditoria de bordas · {view.audit.length}
          </h3>
          <div className="space-y-2 max-h-56 overflow-auto">
            {view.audit.map((issue, i) => (
              <div key={`${issue.clip_id}-${issue.side}-${i}`}
                   className="text-[11px] leading-snug border-t border-red-900/40 pt-2
                              first:border-0 first:pt-0">
                <p className="text-red-200">{issue.message}</p>
                <p className="text-slate-400 mt-0.5">{issue.suggestion_reason}</p>
                <button className="btn btn-xs mt-1.5"
                        onClick={async () => {
                          snapshot()
                          await api.fixAudit(project.id, i)
                          await onChanged()
                          toast('ok', 'Borda corrigida',
                            `movida para ${issue.suggestion.toFixed(3)} s`)
                        }}>
                  corrigir com um clique
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ---------------------------------------------------------- palmas */}
      {view.claps?.some((c) => c.suspect) && (
        <section className="card border-amber-900/60 bg-amber-950/25 p-3">
          <h3 className="text-xs font-semibold text-amber-300 uppercase tracking-wide mb-2">
            Palmas suspeitas
          </h3>
          <p className="hint mb-2">
            Estas falharam só no critério do ataque — pode ser sílaba tônica no meio
            de fala contínua. Confirme você.
          </p>
          {view.claps.filter((c) => c.suspect).map((c) => (
            <div key={c.id} className="text-[11px] border-t border-amber-900/40 pt-2 mt-2
                                       first:border-0 first:pt-0 first:mt-0">
              <div className="font-mono">{timecode(c.time, true)} · pico {c.peak_db} dB
                · salto {c.jump_db} dB</div>
              <p className="text-slate-400 mt-0.5">{c.reason}</p>
              <div className="flex gap-1 mt-1.5">
                <button className="btn btn-xs"
                        onClick={async () => {
                          await api.setClap(project.id, c.id, true)
                          const job = await api.autoedit(project.id)
                          setState({ activeJob: job })
                        }}>
                  é palma
                </button>
                <button className="btn btn-xs"
                        onClick={async () => {
                          await api.setClap(project.id, c.id, false)
                          await onChanged()
                        }}>
                  não é
                </button>
              </div>
            </div>
          ))}
        </section>
      )}

      {/* ------------------------------------------------------ bloco atual */}
      <section className="card p-3">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">
          Bloco selecionado
        </h3>
        {!block && <p className="hint">Clique num bloco na timeline.</p>}
        {block && (
          <div className="space-y-3">
            <div className="text-[11px] font-mono text-slate-400">
              fonte {block.src_start.toFixed(3)}–{block.src_end.toFixed(3)} s
              <br />saída {timecode(block.out_start ?? 0, true)} ·{' '}
              {seconds(block.out_duration)}
            </div>

            <div>
              <label className="label">Seção</label>
              <select className="field py-1 text-xs" value={block.section}
                      onChange={async (e) => {
                        snapshot()
                        await api.setSection(project.id, block.id, e.target.value)
                        await onChanged()
                      }}>
                {Object.entries(SECTIONS).map(([k, v]) => (
                  <option key={k} value={k}>{v.label}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="label">
                Velocidade · <span className="font-mono text-slate-300">
                  {block.speed.toFixed(2)}x</span>
                {block.speed > warnAbove && (
                  <span className="text-amber-400 normal-case ml-1">
                    acima de {warnAbove}x soa artificial
                  </span>
                )}
              </label>
              <input type="range" min={0.9} max={1.4} step={0.01} value={block.speed}
                     disabled={busy} className="w-full"
                     onChange={(e) => setSpeed(Number(e.target.value))} />
              <div className="flex justify-between text-[10px] text-slate-600 font-mono">
                <span>0,90x</span><span>1,25x</span><span>1,40x</span>
              </div>
            </div>

            <div className="flex gap-1.5">
              <button className="btn btn-xs flex-1"
                      onClick={async () => {
                        snapshot()
                        try {
                          const res = await api.splitClip(project.id, block.id, playhead)
                          await onChanged()
                          toast('ok', 'Bloco dividido',
                            `em ${res.src_time.toFixed(3)} s da fonte — os dois lados ` +
                            `continuam contíguos, não é corte`)
                        } catch (e: any) {
                          toast('warn', 'Não deu para dividir', String(e.message ?? e))
                        }
                      }}>
                dividir no playhead
              </button>
              <button className="btn btn-xs flex-1"
                      onClick={async () => {
                        const idx = view.blocks.findIndex((b) => b.id === block.id)
                        const next = view.blocks[idx + 1]
                        if (!next) return
                        snapshot()
                        try {
                          await api.mergeClips(project.id, [block.id, next.id])
                          await onChanged()
                          toast('ok', 'Blocos fundidos')
                        } catch (e: any) {
                          toast('warn', 'Fusão recusada', String(e.message ?? e))
                        }
                      }}>
                fundir com o próximo
              </button>
            </div>

            {(block.snap_in || block.snap_out) && (
              <div className="text-[10px] text-slate-500 space-y-1 border-t border-line pt-2">
                <p className="text-slate-400 font-medium">Como a borda foi encaixada</p>
                {block.snap_in && (
                  <p>entrada: {block.snap_in.original.toFixed(3)} →{' '}
                    {block.snap_in.time.toFixed(3)} s — {block.snap_in.reason}</p>
                )}
                {block.snap_out && (
                  <p>saída: {block.snap_out.original.toFixed(3)} →{' '}
                    {block.snap_out.time.toFixed(3)} s — {block.snap_out.reason}</p>
                )}
              </div>
            )}
          </div>
        )}
      </section>

      {/* ------------------------------------------------ velocidade global */}
      <section className="card p-3">
        <label className="label">
          Velocidade global · <span className="font-mono">{globalSpeed.toFixed(2)}x</span>
        </label>
        <input type="range" min={0.9} max={1.4} step={0.01} value={globalSpeed}
               className="w-full"
               onChange={(e) => setGlobalSpeed(Number(e.target.value))}
               onMouseUp={async (e) => {
                 const value = Number((e.target as HTMLInputElement).value)
                 snapshot()
                 await api.setGlobalSpeed(project.id, value, globalSpeed)
                 setGlobalSpeed(1)
                 await onChanged()
               }} />
        <p className="hint mt-1">
          Multiplica todos os blocos proporcionalmente e volta para 1,00x.
        </p>
      </section>

      {/* ------------------------------------------------------ resumo */}
      <section className="card p-3 text-[11px] text-slate-400 space-y-1">
        <div className="flex justify-between">
          <span>duração final</span>
          <span className="font-mono">{timecode(view.duration, true)}</span>
        </div>
        <div className="flex justify-between">
          <span>fonte</span>
          <span className="font-mono">{timecode(view.source_duration, true)}</span>
        </div>
        <div className="flex justify-between">
          <span>economia</span>
          <span className="font-mono">
            {view.source_duration
              ? `${((1 - view.duration / view.source_duration) * 100).toFixed(0)}%`
              : '—'}
          </span>
        </div>
        <div className="flex justify-between">
          <span>blocos acima de {warnAbove}x</span>
          <span className={`font-mono ${view.speed_warn?.length ? 'text-amber-400' : ''}`}>
            {view.speed_warn?.length ?? 0}
          </span>
        </div>
      </section>
    </div>
  )
}
