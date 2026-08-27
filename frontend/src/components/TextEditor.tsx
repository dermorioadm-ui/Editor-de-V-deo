import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { sourceToOutput } from '../lib/timeline'
import { getState, setState, toast, useStore } from '../state/store'

interface Props { onChanged: () => Promise<any>; snapshot: () => void }

/**
 * Edição pelo texto (Parte 6.3): selecionar palavras e apertar Delete remove
 * o trecho de vídeo. O texto removido fica riscado, não some — dá para desfazer.
 */
export default function TextEditor({ onChanged, snapshot }: Props) {
  const project = useStore((s) => s.project)
  const words = useStore((s) => s.words)
  const removedIds = useStore((s) => s.removedWordIds)
  const fillers = useStore((s) => s.fillers)
  const view = useStore((s) => s.timeline)
  const playhead = useStore((s) => s.playhead)
  const [range, setRange] = useState<[number, number] | null>(null)
  const [anchor, setAnchor] = useState<number | null>(null)
  const [showRemoved, setShowRemoved] = useState(true)
  const [busy, setBusy] = useState(false)
  const activeRef = useRef<HTMLSpanElement>(null)

  const removed = useMemo(() => new Set(removedIds), [removedIds])
  const fillerMap = useMemo(() => {
    const m = new Map<number, any>()
    for (const f of fillers ?? []) for (const id of f.word_ids) m.set(id, f)
    return m
  }, [fillers])

  const activeWord = useMemo(() => {
    if (!view) return -1
    for (const w of words) {
      const out = sourceToOutput(w.start, view.blocks)
      if (out != null && playhead >= out && playhead <= out + (w.end - w.start)) return w.i
    }
    return -1
  }, [words, view, playhead])

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [activeWord])

  const selectedIds = useMemo(() => {
    if (!range) return []
    const [a, b] = range
    const out: number[] = []
    for (let i = Math.min(a, b); i <= Math.max(a, b); i++) out.push(i)
    return out
  }, [range])

  const remove = useCallback(async () => {
    if (!project || !selectedIds.length) return
    const unsafe = selectedIds.map((i) => fillerMap.get(i)).filter((f) => f && !f.safe)
    if (unsafe.length) {
      const f = unsafe[0]
      toast('warn', `“${f.text}” não tem pausa dos dois lados`, f.reason)
    }
    snapshot()
    setBusy(true)
    try {
      const res = await api.removeWords(project.id, selectedIds)
      await onChanged()
      setRange(null)
      const failed = (res.applied ?? []).filter((a: any) => !a.ok)
      if (failed.length) {
        toast('warn', 'Parte da seleção não pôde sair', failed[0].reason)
      } else {
        toast('ok', `${selectedIds.length} palavra(s) removida(s)`,
          (res.applied ?? [])[0]?.explain)
      }
    } catch (e: any) {
      toast('error', 'Falha ao remover', String(e.message ?? e))
    } finally { setBusy(false) }
  }, [project, selectedIds, fillerMap, onChanged, snapshot])

  const restore = useCallback(async () => {
    if (!project || !selectedIds.length) return
    snapshot()
    setBusy(true)
    try {
      await api.restoreWords(project.id, selectedIds)
      await onChanged()
      setRange(null)
      toast('ok', 'Trecho recuperado')
    } finally { setBusy(false) }
  }, [project, selectedIds, onChanged, snapshot])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el?.tagName === 'INPUT' || el?.tagName === 'TEXTAREA') return
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedIds.length) {
        // com uma seleção ativa na timeline, o Delete é dela — os dois
        // handlers juntos removiam palavra E trecho num só aperto
        if (getState().selection) return
        e.preventDefault()
        remove()
      }
      if (e.key === 'Escape') setRange(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedIds, remove])

  const click = (i: number, shift: boolean) => {
    if (shift && anchor != null) setRange([anchor, i])
    else { setAnchor(i); setRange([i, i]) }
    const w = words[i]
    if (view) {
      const out = sourceToOutput(w.start, view.blocks)
      if (out != null) setState({ playhead: out })
    }
  }

  if (!project) return null
  const selectedSet = new Set(selectedIds)
  const unsafeSelected = selectedIds.some((i) => fillerMap.get(i) && !fillerMap.get(i).safe)

  return (
    <div className="p-4 max-w-4xl">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <span className="text-xs text-slate-400">
          Clique numa palavra, Shift+clique para estender, <b>Delete</b> corta o vídeo junto.
        </span>
        <label className="ml-auto text-xs flex items-center gap-1.5 text-slate-400">
          <input type="checkbox" checked={showRemoved}
                 onChange={(e) => setShowRemoved(e.target.checked)} />
          mostrar o que foi removido
        </label>
      </div>

      {selectedIds.length > 0 && (
        <div className="card p-2.5 mb-3 flex items-center gap-2 text-xs">
          <span className="text-slate-300">
            {selectedIds.length} palavra(s):{' '}
            <b className="text-slate-100">
              {selectedIds.map((i) => words[i]?.text).filter(Boolean).join(' ').slice(0, 90)}
            </b>
          </span>
          {unsafeSelected && (
            <span className="chip border-amber-700 text-amber-300">
              sem pausa dos dois lados
            </span>
          )}
          <div className="ml-auto flex gap-1.5">
            <button className="btn btn-xs btn-danger" disabled={busy} onClick={remove}>
              remover trecho
            </button>
            <button className="btn btn-xs" disabled={busy} onClick={restore}>
              recuperar
            </button>
            <button className="btn btn-xs" onClick={() => setRange(null)}>limpar</button>
          </div>
        </div>
      )}

      <div className="card p-4 leading-[2] text-[15px]">
        {words.map((w: any) => {
          const isRemoved = removed.has(w.i)
          if (isRemoved && !showRemoved) return null
          const filler = fillerMap.get(w.i)
          const selected = selectedSet.has(w.i)
          const active = activeWord === w.i
          return (
            <span key={w.i}
                  ref={active ? activeRef : undefined}
                  onClick={(e) => click(w.i, e.shiftKey)}
                  title={filler
                    ? `vício de fala — ${filler.reason}`
                    : `${w.start.toFixed(2)}s · confiança ${(w.prob * 100).toFixed(0)}%`}
                  className={[
                    'cursor-pointer px-0.5 rounded transition',
                    isRemoved ? 'line-through text-slate-600 decoration-red-500/70' : '',
                    selected ? 'bg-accent/30 text-white' : 'hover:bg-ink-600',
                    active ? 'ring-1 ring-accent/70' : '',
                    filler && !isRemoved
                      ? (filler.safe
                        ? 'underline decoration-dotted decoration-amber-400 underline-offset-4'
                        : 'underline decoration-wavy decoration-red-400 underline-offset-4')
                      : '',
                    w.prob < 0.5 && !isRemoved ? 'text-slate-400' : '',
                  ].join(' ')}>
              {w.text}{' '}
            </span>
          )
        })}
      </div>

      {(fillers?.length ?? 0) > 0 && (
        <section className="card p-3 mt-4">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">
            Vícios de fala · {fillers.length}
          </h3>
          <p className="hint mb-2">
            Sublinhado pontilhado = dá para tirar. Ondulado vermelho = tirar quebra a
            palavra vizinha; melhor manter.
          </p>
          <div className="space-y-1.5 max-h-64 overflow-auto">
            {fillers.map((f: any) => {
              const gone = f.word_ids.every((i: number) => removed.has(i))
              return (
                <div key={f.id}
                     className="flex items-center gap-2 text-xs border-t border-line
                                pt-1.5 first:border-0 first:pt-0">
                  <span className="font-mono text-slate-500 w-14">
                    {timecode(f.start)}
                  </span>
                  <span className={gone ? 'line-through text-slate-600' : 'text-slate-200'}>
                    {f.text}
                  </span>
                  <span className={`chip ${f.safe
                    ? 'border-emerald-800 text-emerald-300'
                    : 'border-red-800 text-red-300'}`}>
                    {f.safe ? 'seguro' : 'arriscado'}
                  </span>
                  <span className="text-slate-500 flex-1 truncate">{f.reason}</span>
                  {!gone && (
                    <button className="btn btn-xs"
                            disabled={busy}
                            onClick={async () => {
                              if (!f.safe) {
                                toast('warn', 'Vício limpo é melhor que palavra quebrada',
                                  f.reason)
                              }
                              snapshot()
                              setBusy(true)
                              try {
                                await api.removeWords(project.id, f.word_ids)
                                await onChanged()
                              } finally { setBusy(false) }
                            }}>
                      remover
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      )}
    </div>
  )
}
