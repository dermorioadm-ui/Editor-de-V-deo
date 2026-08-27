import { useState } from 'react'
import Positioner from './Positioner'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { toast, useStore } from '../state/store'

interface Props {
  onChanged: () => Promise<any>
  snapshot: () => void
  safeZone: any
}

const KINDS = [
  { id: 'x', label: '✕ X vermelho' },
  { id: 'circle', label: '◯ círculo' },
  { id: 'arrow', label: '➜ seta' },
  { id: 'dot', label: '● ponto' },
]

/** Fotos inseridas: duração, push-in e marcadores de anotação (Parte 7.2). */
export default function PhotoPanel({ onChanged, snapshot, safeZone }: Props) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const [busy, setBusy] = useState(false)
  if (!project || !view) return null

  const fotos = view.blocks.filter((b) => b.kind === 'photo')
  if (!fotos.length) return null

  const save = async (cid: string, payload: any) => {
    snapshot()
    setBusy(true)
    try {
      await api.updatePhoto(project.id, cid, payload)
      await onChanged()
    } catch (e: any) {
      toast('error', 'Não deu para salvar a foto', String(e.message ?? e))
    } finally { setBusy(false) }
  }

  const aspect = (project.info?.display_width ?? 1080) /
                 (project.info?.display_height ?? 1920)

  return (
    <section className="card p-3">
      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">
        Fotos inseridas · {fotos.length}
      </h3>
      {fotos.map((f) => {
        const photo = f.photo ?? {}
        const kb = photo.ken_burns ?? { enabled: false, intensity: 0.12, direction: 'in' }
        const anns: any[] = photo.annotations ?? []
        return (
          <div key={f.id} className="border-t border-line pt-3 mt-3 first:border-0
                                     first:pt-0 first:mt-0">
            <div className="flex items-start gap-3">
              <Positioner
                frameUrl={api.frameUrl(project.id, (f.out_start ?? 0) + 0.2,
                                       f.source, 300)}
                x={anns[0]?.x ?? 0.5} y={anns[0]?.y ?? 0.5}
                label={anns[0]?.label || (anns.length ? 'marcador' : '')}
                aspect={aspect}
                onChange={(x, y) => {
                  if (!anns.length) return
                  anns[0] = { ...anns[0], x, y }
                }}
                onCommit={(x, y) => {
                  if (!anns.length) return
                  save(f.id, { annotations: anns.map((a, i) =>
                    i === 0 ? { ...a, x, y } : a) })
                }} />

              <div className="flex-1 min-w-0 space-y-2">
                <div className="text-[11px] font-mono text-slate-500">
                  entra em {timecode(f.out_start ?? 0, true)}
                </div>
                <div className="grid grid-cols-3 gap-2">
                  <label className="block">
                    <span className="label">duração (s)</span>
                    <input className="field" type="number" step={0.5} min={0.3}
                           defaultValue={photo.duration ?? 3}
                           onBlur={(e) => save(f.id, { duration: +e.target.value })} />
                  </label>
                  <label className="block">
                    <span className="label">push-in</span>
                    <select className="field" defaultValue={kb.enabled ? 'in' : 'off'}
                            onChange={(e) => save(f.id, {
                              ken_burns: e.target.value === 'off'
                                ? { ...kb, enabled: false }
                                : { ...kb, enabled: true, direction: e.target.value },
                            })}>
                      <option value="off">sem movimento</option>
                      <option value="in">aproximar (Ken Burns)</option>
                      <option value="out">afastar</option>
                    </select>
                  </label>
                  <label className="block">
                    <span className="label">intensidade</span>
                    <input className="field" type="number" step={0.02} min={0} max={0.6}
                           defaultValue={kb.intensity ?? 0.12}
                           onBlur={(e) => save(f.id, {
                             ken_burns: { ...kb, intensity: +e.target.value },
                           })} />
                  </label>
                </div>

                <div className="flex items-center gap-2">
                  <span className="label mb-0">Anotações</span>
                  <button className="btn btn-xs" disabled={busy}
                          onClick={() => save(f.id, {
                            annotations: [...anns, {
                              kind: 'x', x: 0.5, y: 0.4, start: 0.5,
                              label: '', size: 110, color: '#FF2D2D',
                            }],
                          })}>
                    + marcador
                  </button>
                  <span className="hint">acompanham o movimento do zoom</span>
                </div>

                {anns.map((a, i) => (
                  <div key={i} className="grid grid-cols-6 gap-1.5 items-end">
                    <label className="block col-span-2">
                      <span className="label">tipo</span>
                      <select className="field" defaultValue={a.kind}
                              onChange={(e) => save(f.id, {
                                annotations: anns.map((x, k) =>
                                  k === i ? { ...x, kind: e.target.value } : x),
                              })}>
                        {KINDS.map((k) => (
                          <option key={k.id} value={k.id}>{k.label}</option>
                        ))}
                      </select>
                    </label>
                    <label className="block col-span-2">
                      <span className="label">etiqueta</span>
                      <input className="field" defaultValue={a.label ?? ''}
                             onBlur={(e) => save(f.id, {
                               annotations: anns.map((x, k) =>
                                 k === i ? { ...x, label: e.target.value } : x),
                             })} />
                    </label>
                    <label className="block">
                      <span className="label">entra em (s)</span>
                      <input className="field" type="number" step={0.1}
                             defaultValue={a.start ?? 0}
                             onBlur={(e) => save(f.id, {
                               annotations: anns.map((x, k) =>
                                 k === i ? { ...x, start: +e.target.value } : x),
                             })} />
                    </label>
                    <button className="btn btn-xs btn-danger" disabled={busy}
                            onClick={() => save(f.id, {
                              annotations: anns.filter((_, k) => k !== i),
                            })}>
                      remover
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )
      })}
    </section>
  )
}
