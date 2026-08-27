import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { getPlayhead, setPlayhead, subscribePlayhead, toast, useStore }
  from '../state/store'

interface Props { onChanged: () => Promise<any>; snapshot: () => void }

export default function SubtitlePanel({ onChanged, snapshot }: Props) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const listRef = useRef<HTMLDivElement>(null)
  const [corrections, setCorrections] = useState<any[]>([])
  const [novaDe, setNovaDe] = useState('')
  const [novaPara, setNovaPara] = useState('')
  const [style, setStyleState] = useState<any>(project?.plan?.style ?? {})
  const [calib, setCalib] = useState<any>(null)
  const [targetPx, setTargetPx] = useState(726)
  const [sample, setSample] = useState('ISSO MUDA TUDO')
  const [busy, setBusy] = useState(false)

  useEffect(() => { api.corrections().then(setCorrections).catch(() => {}) }, [])
  useEffect(() => { setStyleState(project?.plan?.style ?? {}) }, [project?.plan?.style])

  // A legenda que está tocando é marcada direto no DOM. Antes, cada quadro
  // re-renderizava as 250 legendas (com textarea e tudo) e a aba travava.
  const cues = view?.subtitles
  useEffect(() => {
    const box = listRef.current
    if (!box || !cues?.length) return
    let marked = -1
    const apply = () => {
      const t = getPlayhead()
      let hit = -1
      let lo = 0; let hi = cues.length - 1
      while (lo <= hi) {
        const mid = (lo + hi) >> 1
        if (cues[mid].start <= t) { hit = mid; lo = mid + 1 } else { hi = mid - 1 }
      }
      if (hit >= 0 && t > cues[hit].end) hit = -1
      if (hit === marked) return
      marked = hit
      box.querySelector('[data-cue-active="1"]')?.removeAttribute('data-cue-active')
      if (hit < 0) return
      box.querySelector(`[data-cue="${cues[hit].id}"]`)?.setAttribute('data-cue-active', '1')
    }
    apply()
    return subscribePlayhead(apply)
  }, [cues])

  if (!project || !view) return null

  const saveStyle = async (patch: any) => {
    const next = { ...style, ...patch }
    setStyleState(next)
    snapshot()
    await api.params(project.id, { style: patch, rebuild_subtitles: true })
    await onChanged()
  }

  const applyCorrections = async () => {
    setBusy(true)
    try {
      await api.rebuildSubtitles(project.id)
      await onChanged()
      toast('ok', 'Legendas refeitas com o dicionário atualizado')
    } finally { setBusy(false) }
  }

  return (
    <div className="p-4 grid grid-cols-1 xl:grid-cols-[1fr_360px] gap-4">
      {/* ------------------------------------------------------ lista */}
      <section>
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Legendas · {view.subtitles.length}
          </h3>
          <div className="ml-auto flex gap-1.5">
            <a className="btn btn-xs" href={`/api/projects/${project.id}/subtitles.srt`}>
              baixar .srt
            </a>
            <a className="btn btn-xs" href={`/api/projects/${project.id}/subtitles.ass`}>
              baixar .ass
            </a>
            <button className="btn btn-xs" disabled={busy} onClick={applyCorrections}>
              refazer
            </button>
          </div>
        </div>
        <div ref={listRef} className="card divide-y divide-line max-h-[62vh] overflow-auto">
          {view.subtitles.map((c) => (
            <div key={c.id}
                 data-cue={c.id}
                 className="p-2.5 flex gap-3 data-[cue-active=1]:bg-accent/10">
              <button className="text-[11px] font-mono text-slate-500 w-24 shrink-0
                                 text-left hover:text-accent"
                      onClick={() => setPlayhead(c.start + 0.02)}>
                {timecode(c.start, true)}
                <br />{timecode(c.end, true)}
              </button>
              <div className="flex-1 min-w-0">
                <textarea
                  className="field font-medium leading-snug resize-none w-full"
                  rows={c.text.split('\n').length}
                  defaultValue={c.text}
                  onBlur={async (e) => {
                    if (e.target.value === c.text) return
                    snapshot()
                    await api.editSubtitle(project.id, c.id, { text: e.target.value })
                    await onChanged()
                  }} />
                <div className="flex items-center gap-2 mt-1">
                  {c.text.split('\n').map((line, k) => (
                    <span key={k}
                          className={`text-[10px] font-mono ${
                            line.length > (style.max_chars_per_line ?? 24)
                              ? 'text-amber-400' : 'text-slate-600'}`}>
                      linha {k + 1}: {line.length}
                    </span>
                  ))}
                  <span className={`text-[10px] font-mono ml-auto ${
                    c.end - c.start > (style.max_duration ?? 2.6)
                      ? 'text-amber-400' : 'text-slate-600'}`}>
                    {(c.end - c.start).toFixed(2)} s
                  </span>
                  {c.edited && (
                    <span className="chip border-accent/50 text-accent">editada</span>
                  )}
                </div>
                <div className="flex gap-1 mt-1">
                  <button className="btn btn-xs"
                          onClick={async () => {
                            snapshot()
                            await api.editSubtitle(project.id, c.id,
                              { start: Math.max(0, c.start - 0.1) })
                            await onChanged()
                          }}>◀ início</button>
                  <button className="btn btn-xs"
                          onClick={async () => {
                            snapshot()
                            await api.editSubtitle(project.id, c.id, { start: c.start + 0.1 })
                            await onChanged()
                          }}>início ▶</button>
                  <button className="btn btn-xs"
                          onClick={async () => {
                            snapshot()
                            await api.editSubtitle(project.id, c.id, { end: c.end - 0.1 })
                            await onChanged()
                          }}>◀ fim</button>
                  <button className="btn btn-xs"
                          onClick={async () => {
                            snapshot()
                            await api.editSubtitle(project.id, c.id, { end: c.end + 0.1 })
                            await onChanged()
                          }}>fim ▶</button>
                </div>
              </div>
            </div>
          ))}
          {!view.subtitles.length && (
            <p className="p-4 hint">Nenhuma legenda ainda. Rode a edição automática.</p>
          )}
        </div>
      </section>

      {/* ------------------------------------------------------ estilo */}
      <aside className="space-y-4">
        <section className="card p-3 space-y-2.5">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Estilo
          </h3>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">Fonte</label>
              <input className="field" value={style.font ?? ''}
                     onChange={(e) => setStyleState({ ...style, font: e.target.value })}
                     onBlur={(e) => saveStyle({ font: e.target.value })} />
            </div>
            <div>
              <label className="label">Tamanho</label>
              <input className="field" type="number" value={style.fontsize ?? 64}
                     onChange={(e) => setStyleState({ ...style, fontsize: +e.target.value })}
                     onBlur={(e) => saveStyle({ fontsize: +e.target.value })} />
            </div>
            <div>
              <label className="label">Cor</label>
              <input className="field h-8 p-1" type="color" value={style.primary ?? '#FFFFFF'}
                     onChange={(e) => saveStyle({ primary: e.target.value })} />
            </div>
            <div>
              <label className="label">Contorno</label>
              <input className="field h-8 p-1" type="color"
                     value={style.outline_color ?? '#000000'}
                     onChange={(e) => saveStyle({ outline_color: e.target.value })} />
            </div>
            <div>
              <label className="label">Contorno (px)</label>
              <input className="field" type="number" step={0.5} value={style.outline ?? 4}
                     onChange={(e) => setStyleState({ ...style, outline: +e.target.value })}
                     onBlur={(e) => saveStyle({ outline: +e.target.value })} />
            </div>
            <div>
              <label className="label">Sombra</label>
              <input className="field" type="number" step={0.5} value={style.shadow ?? 1}
                     onChange={(e) => setStyleState({ ...style, shadow: +e.target.value })}
                     onBlur={(e) => saveStyle({ shadow: +e.target.value })} />
            </div>
            <div>
              <label className="label">Altura da faixa (px)</label>
              <input className="field" type="number" value={style.margin_v ?? 220}
                     onChange={(e) => setStyleState({ ...style, margin_v: +e.target.value })}
                     onBlur={(e) => saveStyle({ margin_v: +e.target.value })} />
            </div>
            <div>
              <label className="label">Posição</label>
              <select className="field" value={style.align ?? 2}
                      onChange={(e) => saveStyle({ align: +e.target.value })}>
                <option value={2}>inferior centro</option>
                <option value={8}>topo centro</option>
                <option value={5}>meio centro</option>
                <option value={1}>inferior esquerda</option>
                <option value={3}>inferior direita</option>
              </select>
            </div>
            <div>
              <label className="label">Caracteres por linha</label>
              <input className="field" type="number" value={style.max_chars_per_line ?? 24}
                     onChange={(e) => setStyleState({ ...style, max_chars_per_line: +e.target.value })}
                     onBlur={(e) => saveStyle({ max_chars_per_line: +e.target.value })} />
            </div>
            <div>
              <label className="label">Duração máx. (s)</label>
              <input className="field" type="number" step={0.1} value={style.max_duration ?? 2.6}
                     onChange={(e) => setStyleState({ ...style, max_duration: +e.target.value })}
                     onBlur={(e) => saveStyle({ max_duration: +e.target.value })} />
            </div>
          </div>
          <div className="flex gap-3 text-xs">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={!!style.bold}
                     onChange={(e) => saveStyle({ bold: e.target.checked })} /> negrito
            </label>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={!!style.uppercase}
                     onChange={(e) => saveStyle({ uppercase: e.target.checked })} /> CAIXA ALTA
            </label>
          </div>
        </section>

        <section className="card p-3 space-y-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Calibração por largura
          </h3>
          <p className="hint">
            O fontsize do ASS não vira pixel de forma direta. Diga quantos pixels o texto
            deve ocupar e o sistema mede o render de verdade até acertar.
          </p>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="label">Largura alvo (px)</label>
              <input className="field" type="number" value={targetPx}
                     onChange={(e) => setTargetPx(+e.target.value)} />
            </div>
            <div>
              <label className="label">Texto de teste</label>
              <input className="field" value={sample}
                     onChange={(e) => setSample(e.target.value)} />
            </div>
          </div>
          <button className="btn btn-xs w-full" disabled={busy}
                  onClick={async () => {
                    setBusy(true)
                    try {
                      const res = await api.calibrate(project.id, targetPx, sample)
                      setCalib(res)
                      await onChanged()
                      toast('ok', `fontsize ${res.fontsize}`,
                        `mediu ${res.measured_width} px (alvo ${res.target}, ` +
                        `erro ${res.error_px > 0 ? '+' : ''}${res.error_px} px)`)
                    } catch (e: any) {
                      toast('error', 'Calibração falhou', String(e.message ?? e))
                    } finally { setBusy(false) }
                  }}>
            calibrar e aplicar
          </button>
          {calib && (
            <div className="text-[10px] font-mono text-slate-500 space-y-0.5">
              {calib.history.map((h: any, i: number) => (
                <div key={i}>fontsize {h.fontsize} → {h.width} px</div>
              ))}
            </div>
          )}
        </section>

        <section className="card p-3">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">
            Dicionário de correções
          </h3>
          <p className="hint mb-2">
            Vale entre projetos. Use <code className="font-mono">{'{n}'}</code> para
            qualquer número. A pontuação depois da palavra é preservada.
          </p>
          <div className="flex gap-1.5 mb-2">
            <input className="field text-xs" placeholder="o que o Whisper escreve"
                   value={novaDe} onChange={(e) => setNovaDe(e.target.value)} />
            <span className="self-center text-slate-600">→</span>
            <input className="field text-xs" placeholder="o certo"
                   value={novaPara} onChange={(e) => setNovaPara(e.target.value)} />
            <button className="btn btn-xs"
                    disabled={!novaDe || !novaPara}
                    onClick={async () => {
                      await api.addCorrection(novaDe, novaPara)
                      setNovaDe(''); setNovaPara('')
                      setCorrections(await api.corrections())
                    }}>+</button>
          </div>
          <div className="max-h-56 overflow-auto divide-y divide-line">
            {corrections.map((c) => (
              <div key={c.id} className="flex items-center gap-1.5 py-1 text-xs">
                <input type="checkbox" checked={c.enabled}
                       onChange={async (e) => {
                         await api.updateCorrection(c.id, c.from, c.to, e.target.checked)
                         setCorrections(await api.corrections())
                       }} />
                <span className="font-mono text-slate-400 flex-1 truncate">{c.from}</span>
                <span className="text-slate-600">→</span>
                <span className="font-mono text-slate-200 flex-1 truncate">{c.to}</span>
                <button className="btn btn-xs btn-danger"
                        onClick={async () => {
                          await api.deleteCorrection(c.id)
                          setCorrections(await api.corrections())
                        }}>×</button>
              </div>
            ))}
          </div>
          <button className="btn btn-xs w-full mt-2" disabled={busy}
                  onClick={applyCorrections}>
            aplicar às legendas
          </button>
        </section>
      </aside>
    </div>
  )
}
