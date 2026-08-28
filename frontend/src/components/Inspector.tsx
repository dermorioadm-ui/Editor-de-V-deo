import { useEffect, useMemo, useState } from 'react'
import { SECTIONS } from '../types'
import { api } from '../lib/api'
import { seconds, timecode } from '../lib/format'
import { getPlayhead, setPlayhead, setState, toast, useStore } from '../state/store'

interface Props {
  onChanged: () => Promise<any>
  snapshot: () => void
  onToggleTake: (id: string, restored: boolean) => void
}

export default function Inspector({ onChanged, snapshot, onToggleTake }: Props) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const selectedId = useStore((s) => s.selectedClip)
  const storedGlobal = project?.plan?.speed?.global_multiplier ?? 1
  const [globalSpeed, setGlobalSpeed] = useState(storedGlobal)
  const [busy, setBusy] = useState(false)
  const [dragSpeed, setDragSpeed] = useState<number | null>(null)
  const [dragZoom, setDragZoom] = useState<number | null>(null)
  useEffect(() => { setGlobalSpeed(storedGlobal) }, [storedGlobal])

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
      {/* --------------------------------------------- ajustes automáticos */}
      {(() => {
        const fixed = view.audit_fixed ?? []
        const wfix = view.word_fixes ?? []
        const restam = view.audit?.length ?? 0
        if (!fixed.length && !wfix.length && !restam) return null
        const semCorte = fixed.filter((f) => f.kind === 'sem-corte').length
        const menosRuim = fixed.filter((f) => f.kind === 'menos-ruim').length
        return (
          <section className="card border-emerald-900/50 bg-emerald-950/20 p-3">
            <h3 className="text-xs font-semibold text-emerald-300 uppercase
                           tracking-wide">
              Ajustado sozinho
            </h3>
            <ul className="text-[11px] text-slate-400 mt-1.5 space-y-1">
              {wfix.length > 0 && (
                <li>
                  <b className="text-slate-300">{wfix.length}</b> palavra(s)
                  vinham esticadas por cima de pausa — encaixadas no som, e o
                  silêncio virou corte.
                </li>
              )}
              {fixed.length - semCorte - menosRuim > 0 && (
                <li>
                  <b className="text-slate-300">
                    {fixed.length - semCorte - menosRuim}
                  </b>{' '}
                  borda(s) encaixadas no vale de energia.
                </li>
              )}
              {semCorte > 0 && (
                <li>
                  <b className="text-slate-300">{semCorte}</b> corte(s) não
                  aconteceram: não dava para cortar limpo, então a pausa ficou
                  em vez de comer palavra.
                </li>
              )}
              {menosRuim > 0 && (
                <li>
                  <b className="text-slate-300">{menosRuim}</b> borda(s) sem
                  vale nenhum por perto foram para o ponto mais fraco do áudio.
                </li>
              )}
            </ul>
            {restam > 0 && (
              <p className="hint mt-1.5">
                {restam} borda(s) continuam encostando em fala. Nada a fazer
                automaticamente — se incomodar, arraste na trilha.
              </p>
            )}
            {(fixed.length > 0 || wfix.length > 0) && (
              <details className="mt-1.5">
                <summary className="text-[11px] text-slate-500 cursor-pointer">
                  ver o que mudou
                </summary>
                <div className="space-y-1 mt-1.5 max-h-40 overflow-auto">
                  {wfix.slice(0, 40).map((f, i) => (
                    <p key={`w${i}`} className="text-[11px] text-slate-500 font-mono">
                      “{f.text}” {f.from[0].toFixed(2)}–{f.from[1].toFixed(2)} →{' '}
                      {f.to[0].toFixed(2)}–{f.to[1].toFixed(2)}
                      <span className="font-sans"> (+{f.ganho.toFixed(1)}s de pausa)</span>
                    </p>
                  ))}
                  {fixed.map((f, i) => (
                    <p key={`b${i}`} className="text-[11px] text-slate-500 font-mono">
                      {timecode(f.from, true)} → {timecode(f.to, true)}
                      <span className="font-sans"> · {f.reason}</span>
                    </p>
                  ))}
                </div>
              </details>
            )}
          </section>
        )
      })()}

      {/* ------------------------------------------------------- trilha */}
      {(() => {
        const t = (view.tracks ?? []).find((x) => x.id === 'A1')
        const m = t?.items?.[0]
        if (!m) return null
        return (
          <section className="card p-3">
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-semibold text-slate-400 uppercase
                             tracking-wide">Trilha</h3>
              <span className="text-[11px] text-slate-500 truncate flex-1">
                {m.label}
              </span>
              <button className="btn btn-xs"
                      onClick={async () => {
                        await api.ajustarMusica(project.id, { muted: !m.muted })
                        await onChanged()
                      }}>
                {m.muted ? 'ligar' : 'mudo'}
              </button>
              <button className="btn btn-xs btn-danger"
                      onClick={async () => {
                        await api.deleteItem(project.id, 'music', 'music')
                        await onChanged()
                      }}>
                tirar
              </button>
            </div>
            <label className="label mt-2">
              Volume · <span className="font-mono text-slate-300">
                {(m.gain_db ?? -18).toFixed(0)} dB</span>
              {m.muted && <span className="text-amber-400 normal-case ml-1">
                (no mudo)</span>}
            </label>
            <input type="range" min={-40} max={0} step={1}
                   value={m.gain_db ?? -18} className="w-full" disabled={m.muted}
                   onChange={async (e) => {
                     await api.ajustarMusica(project.id,
                       { gain_db: Number(e.target.value) })
                     await onChanged()
                   }} />
            <label className="flex items-center gap-1.5 text-[11px] text-slate-400 mt-1">
              <input type="checkbox" checked={m.ducking ?? true}
                     onChange={async (e) => {
                       await api.ajustarMusica(project.id,
                         { ducking: e.target.checked })
                       await onChanged()
                     }} />
              abaixar sozinha quando você fala
            </label>
            <p className="hint mt-1">
              Arraste o bloco no trilho para escolher onde ela toca, e as bordas
              para a duração.
            </p>
          </section>
        )
      })()}

      {/* -------------------------------------------- zoom entre cenas */}
      {(() => {
        const z = view.zoom
        if (!z) return null
        const cen = view.zoom_scenes ?? []
        const durs = cen.map((c) => c.duration)
        const media = durs.length
          ? durs.reduce((a, b) => a + b, 0) / durs.length : 0
        const avisos = view.zoom_audit ?? []
        return (
          <section className="card p-3">
            <label className="flex items-center gap-2 text-xs text-slate-300">
              <input type="checkbox" checked={z.enabled}
                     onChange={async (e) => {
                       snapshot()
                       await api.params(project.id,
                                        { zoom: { enabled: e.target.checked } })
                       await onChanged()
                     }} />
              <b>Zoom entre cenas</b>
            </label>
            <p className="hint mt-1">
              Recorte concêntrico no rosto, trocando só em cima de corte. Entra
              no mesmo encode — não custa geração nenhuma.
            </p>

            {z.enabled && (
              <>
                <div className="text-[11px] font-mono text-slate-400 mt-2 space-y-0.5">
                  <div>{cen.length} enquadramentos · média {media.toFixed(1)} s</div>
                  <div>teto da fonte {z.max_zoom.toFixed(2)}x ·{' '}
                    {project.info?.display_width}px de largura</div>
                  <div>rosto {z.face_x.toFixed(2)}, {z.face_y.toFixed(2)}{' '}
                    <span className="text-slate-600">({z.face_method})</span></div>
                </div>

                <label className="label mt-3">
                  Intensidade · <span className="font-mono text-slate-300">
                    {Math.round(z.intensity * 100)}%</span>
                </label>
                <p className="hint mb-1">Multiplica toda a escada de uma vez.</p>
                <input type="range" min={0} max={1.6} step={0.05}
                       value={z.intensity} className="w-full"
                       onChange={async (e) => {
                         await api.params(project.id,
                           { zoom: { intensity: Number(e.target.value) } })
                         await onChanged()
                       }} />

                <label className="label mt-2">
                  Segundos por enquadramento · <span className="font-mono text-slate-300">
                    {z.seconds_per_scene.toFixed(1)} s</span>
                </label>
                <input type="range" min={2} max={9} step={0.1}
                       value={z.seconds_per_scene} className="w-full"
                       onChange={async (e) => {
                         await api.params(project.id,
                           { zoom: { seconds_per_scene: Number(e.target.value) } })
                         await onChanged()
                       }} />

                <div className="grid grid-cols-2 gap-2 mt-2">
                  <div>
                    <label className="label">Rosto X</label>
                    <input type="number" step={0.01} min={0} max={1}
                           className="field py-1 text-xs" value={z.face_x}
                           onChange={async (e) => {
                             await api.params(project.id, { zoom: {
                               face_x: Number(e.target.value),
                               face_method: 'manual' } })
                             await onChanged()
                           }} />
                  </div>
                  <div>
                    <label className="label">Rosto Y</label>
                    <input type="number" step={0.01} min={0} max={1}
                           className="field py-1 text-xs" value={z.face_y}
                           onChange={async (e) => {
                             await api.params(project.id, { zoom: {
                               face_y: Number(e.target.value),
                               face_method: 'manual' } })
                             await onChanged()
                           }} />
                  </div>
                </div>
                <p className="hint mt-1">
                  Todo recorte é concêntrico neste ponto. Se o rosto estiver
                  descentralizado no seu enquadramento, ajuste aqui.
                </p>

                {avisos.length > 0 && (
                  <details className="mt-2">
                    <summary className="text-[11px] text-slate-500 cursor-pointer">
                      {avisos.length} aviso(s) do enquadramento
                    </summary>
                    <div className="space-y-1 mt-1.5 max-h-40 overflow-auto">
                      {avisos.map((a, i) => (
                        <p key={i} className="text-[11px] leading-snug">
                          <span className={a.severity === 'alta' ? 'text-red-300'
                            : a.severity === 'baixa' ? 'text-slate-500'
                            : 'text-amber-300'}>{a.message}</span>
                          <span className="text-slate-600"> — {a.suggestion}</span>
                        </p>
                      ))}
                    </div>
                  </details>
                )}
              </>
            )}
          </section>
        )
      })()}

      {/* ------------------------------------------- o que saiu sozinho */}
      {(() => {
        const takes = (view.takes ?? []).filter((t) => !t.restored)
        const reps = (view.repeats ?? []).filter((r) => !r.restored)
        const voltaram = (view.takes ?? []).filter((t) => t.restored).length
          + (view.repeats ?? []).filter((r) => r.restored).length
        if (!takes.length && !reps.length && !voltaram) return null
        const total = takes.length + reps.length
        return (
          <section className="card p-3">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-1">
              Saiu sozinho · {total}
            </h3>
            <p className="hint mb-2">
              Não perguntei nada. Se errei em algum, clique em <b>voltar</b> —
              corrigir é mais rápido que responder.
            </p>
            <div className="space-y-2 max-h-72 overflow-auto">
              {reps.map((r) => (
                <div key={r.id} className="text-[11px] border-t border-line pt-2
                                           first:border-0 first:pt-0">
                  <div className="flex items-center gap-1.5">
                    <span className="chip border-violet-800 text-violet-300">
                      falou 2x
                    </span>
                    <span className="font-mono text-slate-500">
                      {timecode(r.start, true)}
                    </span>
                    <span className="text-slate-600">
                      {Math.round(r.similarity * 100)}% igual
                    </span>
                  </div>
                  <p className="text-slate-500 line-through mt-1 leading-snug">
                    {r.text.slice(0, 120)}
                  </p>
                  <p className="text-slate-300 mt-0.5 leading-snug">
                    ficou: {r.kept_text.slice(0, 120)}
                  </p>
                  <div className="flex gap-1 mt-1.5">
                    <button className="btn btn-xs"
                            onClick={() => setPlayhead(Math.max(0, r.kept_start - 0.3))}>
                      ouvir a que ficou
                    </button>
                    <button className="btn btn-xs"
                            onClick={async () => {
                              snapshot()
                              await api.setRepeat(project.id, r.id, true)
                              const job = await api.autoedit(project.id)
                              setState({ activeJob: job })
                            }}>
                      voltar
                    </button>
                  </div>
                </div>
              ))}
              {takes.map((t) => (
                <div key={t.id} className="text-[11px] border-t border-line pt-2
                                           first:border-0 first:pt-0">
                  <div className="flex items-center gap-1.5">
                    <span className="chip border-amber-800 text-amber-300">palma</span>
                    <span className="font-mono text-slate-500">
                      {timecode(t.start, true)}–{timecode(t.end, true)}
                    </span>
                  </div>
                  <p className="text-slate-500 line-through mt-1 leading-snug">
                    {(t.text || '(sem texto)').slice(0, 140)}
                  </p>
                  <div className="flex gap-1 mt-1.5">
                    <button className="btn btn-xs"
                            onClick={() => setPlayhead(Math.max(0, t.start - 0.3))}>
                      ouvir
                    </button>
                    <button className="btn btn-xs"
                            onClick={() => onToggleTake(t.id, true)}>
                      voltar
                    </button>
                  </div>
                </div>
              ))}
            </div>
            {voltaram > 0 && (
              <p className="hint mt-2">{voltaram} trecho(s) você já mandou voltar.</p>
            )}
          </section>
        )
      })()}

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
                  {(dragSpeed ?? block.speed).toFixed(2)}x</span>
                {block.speed > warnAbove && (
                  <span className="text-amber-400 normal-case ml-1">
                    acima de {warnAbove}x soa artificial
                  </span>
                )}
              </label>
              <input type="range" min={0.9} max={1.4} step={0.01}
                     value={dragSpeed ?? block.speed} className="w-full"
                     onChange={(e) => setDragSpeed(Number(e.target.value))}
                     onMouseUp={(e) => {
                       const v = Number((e.target as HTMLInputElement).value)
                       setDragSpeed(null)
                       setSpeed(v)
                     }}
                     onTouchEnd={(e) => {
                       const v = Number((e.target as HTMLInputElement).value)
                       setDragSpeed(null)
                       setSpeed(v)
                     }} />
              <div className="flex justify-between text-[10px] text-slate-600 font-mono">
                <span>0,90x</span><span>1,25x</span><span>1,40x</span>
              </div>

            <div>
              <label className="label">
                Enquadramento · <span className="font-mono text-slate-300">
                  {((dragZoom ?? block.zoom ?? 1) * 100).toFixed(0)}%</span>
                {(block.zoom ?? 1) > 1.001 && (
                  <span className="text-slate-500 normal-case ml-1">
                    fechado — o corte deixa de parecer defeito
                  </span>
                )}
              </label>
              <input type="range" min={1} max={view.zoom?.max_zoom ?? 1.25} step={0.01}
                     value={dragZoom ?? block.zoom ?? 1} className="w-full"
                     onChange={(e) => setDragZoom(Number(e.target.value))}
                     onMouseUp={(e) => {
                       const v = Number((e.target as HTMLInputElement).value)
                       setDragZoom(null)
                       api.setZoom(project.id, block.id, v).then(onChanged)
                     }}
                     onTouchEnd={(e) => {
                       const v = Number((e.target as HTMLInputElement).value)
                       setDragZoom(null)
                       api.setZoom(project.id, block.id, v).then(onChanged)
                     }} />
              <label className="flex items-center gap-1.5 text-[11px]
                                text-slate-400 mt-1">
                <input type="checkbox" checked={!!block.zoom_locked}
                       onChange={async (e) => {
                         await api.lockZoom(project.id, block.id, e.target.checked)
                         await onChanged()
                       }} />
                travar (o recálculo automático não mexe)
              </label>
            </div>
            </div>

            <div className="flex gap-1.5">
              <button className="btn btn-xs flex-1"
                      onClick={async () => {
                        snapshot()
                        try {
                          const res = await api.splitClip(project.id, block.id, getPlayhead())
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
                 await api.setGlobalSpeed(project.id, value)
                 await onChanged()
               }} />
        <div className="flex items-center gap-2 mt-1">
          <p className="hint flex-1">
            Multiplica todos os blocos proporcionalmente.
          </p>
          {Math.abs(globalSpeed - 1) > 0.005 && (
            <button className="btn btn-xs"
                    onClick={async () => {
                      snapshot()
                      await api.setGlobalSpeed(project.id, 1.0)
                      await onChanged()
                    }}>
              voltar a 1,00x
            </button>
          )}
        </div>
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
