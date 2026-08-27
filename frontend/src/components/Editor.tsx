import { useCallback, useEffect, useMemo, useState } from 'react'
import Player from './Player'
import Timeline from './Timeline'
import Inspector from './Inspector'
import TextEditor from './TextEditor'
import SubtitlePanel from './SubtitlePanel'
import MediaPanel from './MediaPanel'
import AudioPanel from './AudioPanel'
import ExportPanel from './ExportPanel'
import JobBar from './JobBar'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { getState, pushHistory, setState, toast, useStore } from '../state/store'

const TABS = [
  { id: 'texto', label: 'Texto' },
  { id: 'legendas', label: 'Legendas' },
  { id: 'midia', label: 'Mídia' },
  { id: 'audio', label: 'Áudio' },
  { id: 'exportar', label: 'Exportar' },
] as const

export default function Editor() {
  const project = useStore((s) => s.project)
  const timeline = useStore((s) => s.timeline)
  const envelope = useStore((s) => s.envelope)
  const selection = useStore((s) => s.selection)
  const history = useStore((s) => s.history)
  const future = useStore((s) => s.future)
  const activeJob = useStore((s) => s.activeJob)
  const [tab, setTab] = useState<string>('texto')
  const [presets, setPresets] = useState<any[]>([])
  const [safeZone, setSafeZone] = useState<any>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)

  useEffect(() => { api.presets().then(setPresets).catch(() => {}) }, [])

  const refresh = useCallback(async () => {
    if (!project) return
    const fresh = await api.project(project.id)
    setPreviewUrl(null)
    setState({
      project: fresh,
      timeline: fresh.timeline ?? null,
      words: fresh.analysis?.words ?? [],
      removedWordIds: fresh.analysis?.removed_word_ids ?? [],
      fillers: fresh.analysis?.fillers ?? [],
    })
    if (!getState().envelope && fresh.analysis?.words?.length) {
      const env = await api.envelope(fresh.id).catch(() => null)
      if (env) setState({ envelope: env })
    }
    return fresh
  }, [project])

  useEffect(() => {
    if (activeJob?.kind !== 'previa') return
    if (activeJob.status === 'ok' && activeJob.result?.download) {
      setPreviewUrl(`${activeJob.result.download}?v=${activeJob.id}`)
      setPreviewBusy(false)
      toast('ok', 'Prévia 480p pronta', 'A exportação final continua em qualidade cheia.')
    }
    if (['erro', 'cancelado'].includes(activeJob.status)) setPreviewBusy(false)
  }, [activeJob?.id, activeJob?.status])

  // recarrega quando um job termina
  useEffect(() => {
    if (activeJob && activeJob.status === 'ok' &&
        ['clique-unico', 'analise', 'edicao'].includes(activeJob.kind)) {
      refresh().then(() => {
        toast('ok', `${activeJob.kind} concluída`, activeJob.message)
        setState({ activeJob: null })
      })
    }
  }, [activeJob?.id, activeJob?.status])

  const snapshot = useCallback(() => {
    const s = getState()
    if (project?.plan) {
      pushHistory(project.plan, s.removedWordIds,
        project.analysis?.manual_removed_word_ids ?? [])
    }
  }, [project])

  const currentEntry = useCallback(() => {
    const s = getState()
    return {
      plan: JSON.parse(JSON.stringify(project!.plan)),
      removedWordIds: [...s.removedWordIds],
      manualRemovedWordIds: project!.analysis?.manual_removed_word_ids ?? [],
    }
  }, [project])

  const undo = useCallback(async () => {
    if (!project || !history.length) return
    const prev = history[history.length - 1]
    const now = currentEntry()
    setState((s) => ({
      history: s.history.slice(0, -1),
      future: [...s.future, now],
    }))
    await api.replacePlan(project.id, prev)
    await refresh()
  }, [project, history, refresh, currentEntry])

  const redo = useCallback(async () => {
    if (!project || !future.length) return
    const next = future[future.length - 1]
    const now = currentEntry()
    setState((s) => ({
      future: s.future.slice(0, -1),
      history: [...s.history, now],
    }))
    await api.replacePlan(project.id, next)
    await refresh()
  }, [project, future, refresh, currentEntry])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el?.tagName === 'INPUT' || el?.tagName === 'TEXTAREA' || el?.isContentEditable) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo(); else undo()
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); redo() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undo, redo])

  const deleteSelection = useCallback(async () => {
    if (!project || !selection || !timeline) return
    // a seleção vem no eixo da FONTE; converte para saída
    const toOut = (t: number) => {
      for (const b of timeline.blocks) {
        if (t >= b.src_start && t <= b.src_end) {
          const scale = ((b.out_end ?? 0) - (b.out_start ?? 0)) / Math.max(b.src_duration, 1e-9)
          return (b.out_start ?? 0) + (t - b.src_start) * scale
        }
      }
      return null
    }
    const a = toOut(Math.min(selection.start, selection.end))
    const b = toOut(Math.max(selection.start, selection.end))
    if (a == null || b == null) {
      toast('warn', 'Seleção fora de um bloco',
        'A seleção precisa cair sobre um trecho que ainda existe na linha do tempo.')
      return
    }
    snapshot()
    try {
      const res = await api.deleteRange(project.id, a, b)
      setState({ selection: null })
      await refresh()
      toast('ok', 'Trecho removido', (res.explain ?? []).join('\n'))
    } catch (e: any) {
      toast('error', 'Não deu para remover', String(e.message ?? e))
    }
  }, [project, selection, timeline, refresh, snapshot])

  const restore = useCallback(async (start: number, end: number) => {
    if (!project) return
    snapshot()
    await api.restoreRange(project.id, start, end)
    await refresh()
    toast('ok', 'Trecho recuperado', `${timecode(start, true)} → ${timecode(end, true)}`)
  }, [project, refresh, snapshot])

  const analysed = ((project?.analysis?.words?.length ?? 0) > 0)

  const runOneClick = async () => {
    if (!project) return
    // já analisado = a transcrição está pronta; refazer só a proposta de
    // corte leva segundos. Retranscrever custaria minutos à toa.
    const job = analysed
      ? await api.autoedit(project.id)
      : await api.oneclick(project.id)
    setState({ activeJob: job })
  }

  const applyPreset = async (name: string) => {
    if (!project) return
    snapshot()
    await api.applyPreset(project.id, name)
    const job = await api.autoedit(project.id)
    setState({ activeJob: job })
  }

  useEffect(() => {
    if (project?.id) api.safeZone(project.id).then(setSafeZone).catch(() => {})
  }, [project?.id])

  if (!project) return null
  const view = timeline

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <header className="flex items-center gap-3 px-4 h-12 border-b border-line bg-ink-800">
        <button className="btn btn-xs"
                onClick={() => setState({ view: 'home', project: null })}>
          ← projetos
        </button>
        <h1 className="font-medium text-sm truncate max-w-xs">{project.name}</h1>
        <span className="text-[11px] text-slate-500 font-mono truncate max-w-md">
          {project.info?.display_width}×{project.info?.display_height}
          {' · '}{project.info?.fps?.toFixed(2)} fps
          {' · '}{timecode(project.info?.duration ?? 0)}
          {project.info?.is_hdr ? ' · HDR' : ''}
        </span>
        <select className="field w-40 py-1 text-xs" value={project.preset}
                onChange={(e) => applyPreset(e.target.value)}>
          {presets.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
        </select>
        <div className="ml-auto flex items-center gap-2">
          <button className="btn btn-xs" disabled={!history.length} onClick={undo}
                  title="Ctrl+Z">↶ desfazer</button>
          <button className="btn btn-xs" disabled={!future.length} onClick={redo}
                  title="Ctrl+Shift+Z">↷ refazer</button>
          <button className="btn btn-primary btn-xs" onClick={runOneClick}
                  disabled={!!activeJob &&
                            ['fila', 'rodando'].includes(activeJob.status)}>
            {analysed ? 'refazer edição' : 'EDITAR'}
          </button>
        </div>
      </header>

      <JobBar />

      <div className="flex-1 flex min-h-0">
        <aside className="w-[320px] shrink-0 border-r border-line p-3 flex flex-col gap-3
                          min-h-0 overflow-hidden">
          <Player projectId={project.id}
                  blocks={view?.blocks ?? []}
                  cues={view?.subtitles ?? []}
                  duration={view?.duration ?? project.info?.duration ?? 0}
                  style={project.plan?.style}
                  previewUrl={previewUrl}
                  previewBusy={previewBusy}
                  onRequestPreview={async () => {
                    setPreviewBusy(true)
                    setPreviewUrl(null)
                    const job = await api.preview(project.id)
                    setState({ activeJob: job })
                  }}
                  safeZone={safeZone?.band?.found
                    ? { top: safeZone.band.top, bottom: safeZone.band.bottom } : null} />
        </aside>

        <main className="flex-1 flex flex-col min-w-0 min-h-0">
          <nav className="flex items-center gap-1 px-3 border-b border-line bg-ink-800
                          overflow-x-auto">
            {TABS.map((t) => (
              <button key={t.id}
                      className={`tab ${tab === t.id ? 'tab-active' : ''}`}
                      onClick={() => setTab(t.id)}>
                {t.label}
              </button>
            ))}
            {view && (
              <span className="ml-auto text-[11px] text-slate-500 font-mono pr-2">
                {view.blocks.length} blocos · {timecode(view.duration)} de{' '}
                {timecode(view.source_duration)}
                {view.audit?.length ? ` · ${view.audit.length} alerta(s)` : ''}
              </span>
            )}
          </nav>
          <div className="flex-1 overflow-auto min-h-0">
            {!analysed && (
              <div className="p-8 text-center text-slate-400">
                <p className="text-sm">
                  Este projeto ainda não foi analisado. Aperte <b>EDITAR</b> no topo.
                </p>
              </div>
            )}
            {analysed && tab === 'texto' && <TextEditor onChanged={refresh} snapshot={snapshot} />}
            {analysed && tab === 'legendas' && <SubtitlePanel onChanged={refresh} snapshot={snapshot} />}
            {analysed && tab === 'midia' && <MediaPanel onChanged={refresh} snapshot={snapshot} safeZone={safeZone} />}
            {analysed && tab === 'audio' && <AudioPanel onChanged={refresh} />}
            {analysed && tab === 'exportar' && <ExportPanel onChanged={refresh} />}
          </div>
        </main>

        <aside className="w-[300px] shrink-0 border-l border-line overflow-auto">
          <Inspector onChanged={refresh} snapshot={snapshot} />
        </aside>
      </div>

      {view && (
        <Timeline view={view} envelope={envelope}
                  sourceDuration={view.source_duration || project.info?.duration || 0}
                  onDeleteSelection={deleteSelection}
                  onRestore={restore}
                  onToggleTake={async (id, restored) => {
                    snapshot()
                    await api.setTake(project.id, id, restored)
                    const job = await api.autoedit(project.id)
                    setState({ activeJob: job })
                  }}
                  onToggleClap={async (id, enabled) => {
                    await api.setClap(project.id, id, enabled)
                    await refresh()
                  }}
                  onSubtitleEdge={async (cueId, side, outTime) => {
                    snapshot()
                    await api.editSubtitle(project.id, cueId,
                      { [side]: Number(outTime.toFixed(3)) })
                    await refresh()
                  }} />
      )}
    </div>
  )
}
