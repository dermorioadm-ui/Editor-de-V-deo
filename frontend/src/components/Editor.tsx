import { useCallback, useEffect, useRef, useState } from 'react'
import Player from './Player'
import Timeline from './Timeline'
import Inspector from './Inspector'
import TextEditor from './TextEditor'
import SubtitlePanel from './SubtitlePanel'
import MediaPanel from './MediaPanel'
import LookPanel from './LookPanel'
import AudioPanel from './AudioPanel'
import AIPanel from './AIPanel'
import ProcessingView from './ProcessingView'
import ExportPanel from './ExportPanel'
import JobBar from './JobBar'
import { api } from '../lib/api'
import { sourceToOutput } from '../lib/timeline'
import { timecode } from '../lib/format'
import { getPlayhead, getState, pushHistory, setPlayhead, setState, toast, useStore }
  from '../state/store'

const TABS = [
  { id: 'texto', label: 'Texto' },
  { id: 'legendas', label: 'Legendas' },
  { id: 'midia', label: 'Mídia' },
  { id: 'filtro', label: 'Filtro' },
  { id: 'audio', label: 'Áudio' },
  { id: 'ia', label: 'IA' },
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
  const [tab, setTab] = useState<string | null>(null)
  const [presets, setPresets] = useState<any[]>([])
  const [safeZone, setSafeZone] = useState<any>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  // a prévia renderizada nao acompanha edicao ao vivo: marca-se velha e ela
  // se refaz sozinha, enquanto o player cai na copia leve para nao ficar preso
  const [previaVelha, setPreviaVelha] = useState(false)
  // o arquivo FINAL que o clique único já exportou. Ter isso pronto é a
  // diferença entre "o editor abriu" e "o vídeo está pronto".
  const [baixar, setBaixar] = useState<string | null>(null)
  const [baixarVelho, setBaixarVelho] = useState(false)
  // o job que está gerando o MP4 final POR BAIXO — o editor já está de pé
  const [exportando, setExportando] = useState<any>(null)
  const [outrosFormatos, setOutrosFormatos] =
    useState<{ aspecto: string; url: string }[]>([])
  const edicoes = useRef(0)            // quantos retoques houve
  const edicoesNoExport = useRef(-1)   // quantos havia quando a exportação começou
  const [previewBusy, setPreviewBusy] = useState(false)
  const playing = useStore((s) => s.playing)
  const [proxyUrl, setProxyUrl] = useState<string | null>(null)

  useEffect(() => { api.presets().then(setPresets).catch(() => {}) }, [])

  const refresh = useCallback(async () => {
    if (!project) return
    const fresh = await api.project(project.id)
    setPreviewUrl(null)
    const dur = fresh.timeline?.duration ?? 0
    setPlayhead(Math.min(getPlayhead(), Math.max(0, dur - 0.01)))
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
      setPreviaVelha(false)
    }
    if (['erro', 'cancelado'].includes(activeJob.status)) setPreviewBusy(false)
  }, [activeJob?.id, activeJob?.status])

  // O clique único já entrega a prévia da edição renderizada: é ela que toca
  // sem tranco (arquivo linear, zero busca) e com o zoom e a legenda queimados.
  useEffect(() => {
    if (activeJob?.kind !== 'clique-unico' || activeJob.status !== 'ok') return
    const url = activeJob.result?.previa?.download
    if (url) { setPreviewUrl(`${url}?v=${activeJob.id}`); setPreviaVelha(false) }
  }, [activeJob?.id, activeJob?.status])

  // O MP4 FINAL é gerado por baixo, depois que esta tela já abriu. Este efeito
  // é o que faz o botão "baixar o vídeo" acender sozinho quando ele fica
  // pronto — e voltar a dizer "gerando…" a cada retoque.
  const jobs = useStore((s) => s.jobs)
  useEffect(() => {
    if (!project) return
    const meus = Object.values(jobs ?? {}).filter((j: any) =>
      j.kind === 'exportacao' && j.project_id === project.id)
    if (!meus.length) return
    const ultimo: any = meus.reduce((a: any, b: any) =>
      (b.created_at ?? 0) >= (a.created_at ?? 0) ? b : a)
    setExportando(['fila', 'rodando'].includes(ultimo.status) ? ultimo : null)
    if (ultimo.status === 'ok' && ultimo.result?.download) {
      setBaixar(`${ultimo.result.download}?v=${ultimo.id}`)
      // os formatos extras (quadrado, horizontal) do MESMO corte
      setOutrosFormatos((ultimo.result.formatos ?? [])
        .filter((f: any) => f.aspecto && f.aspecto !== 'fonte' && f.download)
        .map((f: any) => ({ aspecto: f.aspecto,
                            url: `${f.download}?v=${ultimo.id}` })))
      // se o usuário editou DURANTE a exportação, o arquivo que acabou de
      // sair já nasceu velho — e o debounce dispara outra. Contador local, não
      // relógio: o do servidor é outra máquina.
      setBaixarVelho(edicoes.current !== edicoesNoExport.current)
    }
  }, [jobs, project?.id])

  // A CÓPIA LEVE DA FONTE é feita no PRIMEIRO retoque, não no clique único.
  // Ela só serve enquanto a prévia da edição está sendo refeita — antes do
  // primeiro retoque isso nunca acontece, então gerá-la lá atrás era um passe
  // inteiro sobre a fonte entre o usuário e o vídeo dele.
  useEffect(() => {
    if (!previaVelha || !project || proxyUrl) return
    api.proxyStatus(project.id).then((st) => {
      if (!st.ok && st.precisa) api.buildProxy(project.id).catch(() => {})
    }).catch(() => {})
  }, [previaVelha, project?.id, proxyUrl])

  // Editou? A prévia renderizada ficou velha. Ela se refaz sozinha, depois de
  // uns segundos parado — refazer a cada clique seria uma fila de renders.
  useEffect(() => {
    if (!previewUrl || !previaVelha || !project) return
    const t = window.setTimeout(async () => {
      try {
        setPreviewBusy(true)
        const job = await api.preview(project.id, { scale: '240', crf: 32 })
        setState({ activeJob: job })
      } catch { setPreviewBusy(false) }
    }, 4000)
    return () => window.clearTimeout(t)
  }, [previaVelha, previewUrl, project?.id])

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

  // qualquer mudança na linha do tempo envelhece a prévia renderizada
  const marcarPreviaVelha = useCallback(() => {
    edicoes.current += 1
    setPreviaVelha(true)
    setBaixarVelho(true)      // editou: o arquivo exportado ficou velho
  }, [])

  // O RETOQUE TAMBÉM SE ENTREGA SOZINHO. Uns segundos parado e o arquivo
  // final se refaz por baixo. É barato porque o render guarda cada trecho por
  // hash de conteúdo: só o que o retoque tocou é reencodado — o resto é
  // reaproveitado do disco.
  useEffect(() => {
    if (!project || !baixarVelho || exportando) return
    const t = window.setTimeout(async () => {
      try {
        edicoesNoExport.current = edicoes.current
        await api.exportFinal(project.id)
      } catch { /* a fila responde no próximo retoque */ }
    }, 6000)
    return () => window.clearTimeout(t)
  }, [baixarVelho, exportando, project?.id])

  const snapshot = useCallback(() => {
    marcarPreviaVelha()
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

  const opBusy = useRef(false)

  const undo = useCallback(async () => {
    if (!project || !history.length || opBusy.current) return
    opBusy.current = true
    try {
    const prev = history[history.length - 1]
    const now = currentEntry()
    setState((s) => ({
      history: s.history.slice(0, -1),
      future: [...s.future, now],
    }))
    await api.replacePlan(project.id, prev)
    await refresh()
    } finally { opBusy.current = false }
  }, [project, history, refresh, currentEntry])

  const redo = useCallback(async () => {
    if (!project || !future.length || opBusy.current) return
    opBusy.current = true
    try {
    const next = future[future.length - 1]
    const now = currentEntry()
    setState((s) => ({
      future: s.future.slice(0, -1),
      history: [...s.history, now],
    }))
    await api.replacePlan(project.id, next)
    await refresh()
    } finally { opBusy.current = false }
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
    // a seleção vem no eixo da FONTE PRINCIPAL; blocos de inserto/foto têm
    // src_start no eixo da própria mídia e casariam com o tempo errado
    const a = sourceToOutput(Math.min(selection.start, selection.end),
                             timeline.blocks, 'main')
    const b = sourceToOutput(Math.max(selection.start, selection.end),
                             timeline.blocks, 'main')
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

  // Apagar o bloco inteiro: o gesto do CapCut. Vira um delete-range na
  // janela de saída do bloco, então tudo que já existe (remap de overlays,
  // reconstrução de legenda, desfazer) continua valendo.
  const deleteClip = useCallback(async (clipId: string) => {
    if (!project || !timeline) return
    const b = timeline.blocks.find((x) => x.id === clipId)
    if (!b || b.out_start == null || b.out_end == null) return
    snapshot()
    try {
      await api.deleteRange(project.id, b.out_start + 0.002, b.out_end - 0.002)
      setState({ selectedClip: null })
      await refresh()
      toast('ok', 'Bloco apagado',
        `${timecode(b.out_start, true)} → ${timecode(b.out_end, true)}`)
    } catch (e: any) {
      toast('error', 'Não deu para apagar', String(e.message ?? e))
    }
  }, [project, timeline, refresh, snapshot])

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

  // Prévia leve: uma cópia 480p da FONTE, feita uma vez. É o que o CapCut faz
  // para o play não engasgar num arquivo de 1080x1920 a 60 fps.
  useEffect(() => {
    if (!project?.id) return
    let vivo = true
    const ver = async () => {
      try {
        const st = await api.proxyStatus(project.id)
        if (!vivo) return
        if (st.ok) {
          setProxyUrl(`/api/projects/${project.id}/proxy`)
          return true
        }
        setProxyUrl(null)
        return false
      } catch { return false }
    }
    ver().then((pronto) => {
      // ainda não existe: manda fazer, e a barra de jobs mostra o progresso
      if (!pronto && vivo && analysed) api.buildProxy(project.id).catch(() => {})
    })
    return () => { vivo = false }
  }, [project?.id, analysed])

  // quando o job do proxy termina, o player passa a tocar a cópia leve
  useEffect(() => {
    if (activeJob?.kind !== 'proxy' || activeJob.status !== 'ok') return
    api.proxyStatus(project!.id).then((st) => {
      if (st.ok) {
        setProxyUrl(`/api/projects/${project!.id}/proxy`)
        toast('ok', 'Prévia leve pronta',
          'O play não engasga mais. A exportação continua lendo o arquivo original.')
      }
    }).catch(() => {})
  }, [activeJob?.id, activeJob?.status])

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

      {(() => {
        // Só o pipeline de ENTRADA esconde o editor: o refazer-edição de um
        // retoque (kind 'edicao') roda a cada toggle e não pode piscar a tela
        // inteira — ele mostra progresso na JobBar, com o editor de pé.
        // conferir o project_id é obrigatório: activeJob é global e o
        // WebSocket transmite jobs de TODOS os projetos — sem o filtro, o
        // clique-único do projeto A sequestrava o editor do projeto B
        const pipeline = activeJob
          && activeJob.project_id === project.id
          && ['clique-unico', 'analise'].includes(activeJob.kind)
          && ['fila', 'rodando', 'erro', 'ok'].includes(activeJob.status)
          && !(activeJob.status === 'ok' && analysed)
        if (pipeline || !analysed) {
          return <ProcessingView />
        }
        return null
      })() || <>

      {/* O QUE A IA FEZ NESTE VÍDEO — sempre à vista, nunca escondido numa aba.
          Era a maior queixa e ela era justa: tudo isto já era calculado e
          jogado fora, então não havia como saber se a IA tinha botado a mão
          no vídeo. E não saber é o mesmo que ela não ter botado. */}
      {analysed && (() => {
        const ia = project.analysis?.ai_cortes
        if (!ia || !ia.rodou) {
          const motivo = ia?.erro === 'sem chave'
            ? 'faltava a chave do Gemini'
            : ia?.erro === 'desligada'
            ? 'a IA está desligada nos ajustes'
            : ia?.erro ? `a IA falhou: ${ia.erro}` : 'a IA não rodou'
          return (
            <div className="flex items-center gap-3 px-4 py-2 text-xs border-b
                            border-red-900/60 bg-red-950/30">
              <span className="text-red-200 font-medium">
                A IA NÃO cortou este vídeo
              </span>
              <span className="text-slate-400">
                {motivo} — o corte que você está vendo é só a regra do programa
                (silêncio, palma, assobio, comando falado).
              </span>
              <button className="btn btn-xs ml-auto"
                      onClick={() => setTab('ia')}>resolver</button>
            </div>
          )
        }
        const r = ia.resumo ?? {}
        return (
          <div className="px-4 py-2 text-xs border-b border-sky-900/50
                          bg-sky-950/20">
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-sky-200 font-medium">
                A IA leu e cortou · <span className="font-mono">{ia.modelo}</span>
              </span>
              <span className="text-slate-300">
                <b>{r.refeito ?? 0}</b> trecho(s) refeito(s) e{' '}
                <b>{r.copy ?? 0}</b> de copy fora
                {typeof r.palavras_fora === 'number' && typeof r.palavras === 'number'
                  && r.palavras > 0 &&
                  ` (${r.palavras_fora} de ${r.palavras} palavras)`}
                {' · '}<b>{r.secoes ?? 0}</b> etapa(s) de ritmo
                {' · '}câmera em <b>{r.camera ?? 0}</b>
                {(r.fechado ?? 0) > 0 && ` (${r.fechado} fechando)`}
              </span>
              {(r.recusados ?? 0) > 0 && (
                <span className="text-amber-300">
                  {r.recusados} corte(s) que ela pediu eu recusei
                </span>
              )}
              <button className="btn btn-xs ml-auto"
                      onClick={() => setTab('ia')}>ver o que ela decidiu</button>
            </div>
            {ia.leitura && (
              <p className="text-slate-500 mt-0.5 italic truncate">
                “{ia.leitura}”
              </p>
            )}
            {ia.modelo_trocado_de && (
              <p className="text-amber-300 mt-0.5">
                atenção: o modelo que você fixou ({ia.modelo_trocado_de}) não
                está mais disponível nessa chave — rodou com {ia.modelo}.
              </p>
            )}
          </div>
        )
      })()}

      {analysed && view && (() => {
        // O veredito. O usuário reclamou que o editor não entregava pronto:
        // ou está pronto e ele exporta, ou aqui diz exatamente o que falta.
        // Palma não pergunta mais nada, então o único pendente possível é a
        // borda que nem desfazendo o corte ficou limpa.
        const pend = view.audit?.length ?? 0
        const fixed = view.audit_fixed?.length ?? 0
        const auto = (view.takes ?? []).filter((t) => !t.restored).length
          + (view.repeats ?? []).filter((r) => !r.restored).length
        const zoom = view.blocks.filter((b) => (b.zoom ?? 1) > 1.001).length
        const econ = view.source_duration > 0
          ? Math.round((1 - view.duration / view.source_duration) * 100) : 0
        if (pend === 0) {
          return (
            <div className="flex items-center gap-3 px-4 py-2 text-xs border-b
                            border-emerald-900/50 bg-emerald-950/25">
              <span className="text-emerald-300 font-medium">✓ Pronto para exportar</span>
              {/* TUDO O QUE FOI APLICADO, numa linha só. Ele pediu para ver
                  o que o programa fez sem ter que abrir aba por aba. */}
              <span className="text-slate-400">
                {timecode(view.duration)} de {timecode(view.source_duration)}{' '}
                ({econ}% mais curto) · {view.blocks.length} blocos ·{' '}
                {view.subtitles.length} legendas
                {auto > 0 && ` · ${auto} trecho(s) ruim(ns) fora`}
                {zoom > 0 && ` · zoom em ${zoom}`}
                {fixed > 0 && ` · ${fixed} borda(s) acertadas`}
                {project.plan?.look && project.plan.look !== 'nenhum'
                  && ` · filtro ${project.plan.look}`}
                {(project.plan?.speed?.global_multiplier ?? 1) > 1.001
                  && ` · +${Math.round(
                    (project.plan.speed.global_multiplier - 1) * 100)}% de ritmo`}
                {project.plan?.music?.enabled && ` · trilha ${
                  project.plan.music.curva?.length
                    ? `com ${project.plan.music.curva.length} mudança(s) de volume`
                    : 'de fundo'}`}
                {(project.plan?.export?.fps ?? 0) > 0
                  && ` · ${project.plan.export.fps} fps`}
                {project.plan?.export?.extras?.length > 0
                  && ` · também em ${project.plan.export.extras.join(' e ')}`}
              </span>
              {/* O arquivo se entrega sozinho: gera por baixo quando o editor
                  abre e se refaz sozinho quando você para de retocar. O
                  botão só some enquanto está gerando. */}
              {previewBusy ? (
                <span className="ml-auto text-slate-400 flex items-center gap-1.5">
                  <span className="inline-block h-1.5 w-1.5 rounded-full
                                   bg-sky-400 animate-pulse" />
                  refazendo a prévia com o seu retoque…
                </span>
              ) : exportando ? (
                <span className="ml-auto text-slate-400 flex items-center gap-1.5">
                  <span className="inline-block h-1.5 w-1.5 rounded-full
                                   bg-emerald-400 animate-pulse" />
                  gerando o arquivo final… {Math.round((exportando.progress ?? 0) * 100)}%
                </span>
              ) : baixar && !baixarVelho ? (
                <span className="ml-auto flex items-center gap-2">
                  <a className="btn btn-primary btn-xs" href={baixar} download>
                    ↓ baixar o vídeo
                  </a>
                  {outrosFormatos.map((f) => (
                    <a key={f.aspecto} className="btn btn-xs" href={f.url} download>
                      {f.aspecto}
                    </a>
                  ))}
                </span>
              ) : baixarVelho ? (
                <span className="ml-auto text-slate-500">
                  refazendo o arquivo com o seu retoque…
                </span>
              ) : (
                <button className="btn btn-primary btn-xs ml-auto"
                        onClick={() => setTab('exportar')}>
                  exportar →
                </button>
              )}
            </div>
          )
        }
        return (
          <div className="flex items-center gap-3 px-4 py-2 text-xs border-b
                          border-amber-900/50 bg-amber-950/25">
            <span className="text-amber-200 font-medium">
              Falta você decidir {pend} coisa(s)
            </span>
            <span className="text-slate-400">
              {view.audit!.length} corte(s) sem respiro por perto
              {fixed > 0 && ` · ${fixed} borda(s) já resolvidas sozinhas`}
              {auto > 0 && ` · ${auto} trecho(s) ruim(ns) já foram fora`}
            </span>
            <span className="ml-auto flex gap-1.5">
              <button className="btn btn-xs" onClick={() => setTab('exportar')}>
                exportar assim mesmo
              </button>
            </span>
          </div>
        )
      })()}

      <div className="flex-1 flex min-h-0">
        {/* rail de ferramentas: o painel só abre quando pedido — o vídeo
            é o centro da tela, como em qualquer editor de verdade */}
        <nav className="w-[72px] shrink-0 border-r border-line bg-ink-800
                        flex flex-col py-2 gap-0.5 overflow-y-auto">
          {TABS.map((t) => (
            <button key={t.id}
                    className={`mx-1.5 rounded-md px-1 py-2.5 text-[11px]
                      leading-tight transition-colors cursor-pointer
                      ${tab === t.id
                        ? 'bg-accent/15 text-accent font-medium'
                        : 'text-slate-400 hover:text-slate-200 hover:bg-ink-700'}`}
                    onClick={() => setTab(tab === t.id ? null : t.id)}>
              {t.label}
            </button>
          ))}
        </nav>

        {tab && (
          <aside className="w-[400px] shrink-0 border-r border-line overflow-auto
                            min-h-0">
            {tab === 'texto' && <TextEditor onChanged={refresh} snapshot={snapshot} />}
            {tab === 'legendas' && <SubtitlePanel onChanged={refresh} snapshot={snapshot} />}
            {tab === 'midia' && <MediaPanel onChanged={refresh} snapshot={snapshot} safeZone={safeZone} />}
            {tab === 'filtro' && <LookPanel onChanged={refresh} snapshot={snapshot} />}
            {tab === 'audio' && <AudioPanel onChanged={refresh} />}
            {tab === 'ia' && <AIPanel onChanged={refresh} />}
            {tab === 'exportar' && <ExportPanel onChanged={refresh} />}
          </aside>
        )}

        <main className="flex-1 flex flex-col min-w-0 min-h-0 p-3">
          <Player projectId={project.id}
                  blocks={view?.blocks ?? []}
                  cues={view?.subtitles ?? []}
                  duration={view?.duration ?? project.info?.duration ?? 0}
                  style={project.plan?.style}
                  zoomAnchor={project.plan?.zoom
                    ? { x: project.plan.zoom.face_x ?? 0.5,
                        y: project.plan.zoom.face_y ?? 0.4 } : null}
                  sourceSize={project.info
                    ? [project.info.display_width || project.info.width,
                       project.info.display_height || project.info.height]
                    : null}
                  previewUrl={previaVelha ? null : previewUrl}
                  previaVelha={previaVelha}
                  proxyUrl={proxyUrl}
                  previewBusy={previewBusy}
                  onRequestPreview={async () => {
                    setPreviewBusy(true)
                    setPreviewUrl(null)
                    const job = await api.preview(project.id,
                      { scale: '240', crf: 32 })
                    setState({ activeJob: job })
                  }}
                  safeZone={safeZone?.band?.found
                    ? { top: safeZone.band.top, bottom: safeZone.band.bottom } : null} />
        </main>

        <aside className="w-[300px] shrink-0 border-l border-line overflow-auto">
          <Inspector onChanged={refresh} snapshot={snapshot}
                     onToggleTake={async (id, restored) => {
                       snapshot()
                       await api.setTake(project.id, id, restored)
                       const job = await api.autoedit(project.id)
                       setState({ activeJob: job })
                     }} />
        </aside>
      </div>

      {view && (
        <Timeline view={view} envelope={envelope}
                  sourceDuration={view.source_duration || project.info?.duration || 0}
                  onDeleteSelection={deleteSelection}
                  onDeleteClip={deleteClip}
                  playing={playing}
                  onTogglePlay={() => setState((s) => ({ playRequest: s.playRequest + 1 }))}
                  onMoveItem={async (kind, id, side, delta) => {
                    snapshot()
                    try {
                      if (side === 'move') {
                        await api.moveItem(project.id, kind, id, delta)
                      } else {
                        const it = (timeline?.tracks ?? [])
                          .flatMap((t) => t.items).find((x) => x.id === id)
                        if (!it) return
                        const base = side === 'start' ? it.out_start : it.out_end
                        await api.resizeItem(project.id, kind, id, side, base + delta)
                      }
                      await refresh()
                    } catch (e: any) {
                      toast('warn', 'Não deu para mover', String(e.message ?? e))
                    }
                  }}
                  onDeleteItem={async (kind, id) => {
                    snapshot()
                    await api.deleteItem(project.id, kind, id)
                    await refresh()
                    toast('ok', 'Item removido do trilho')
                  }}
                  onDropFile={async (trackId, file) => {
                    // O navegador entrega só nome e tamanho — nunca o caminho.
                    // Em vez de subir o arquivo, procuramos ele no disco pelo
                    // nome, que é o mesmo caminho que a tela inicial usa.
                    const ext = (file.name.split('.').pop() || '').toLowerCase()
                    const tipos: Record<string, string> = {
                      mp3: 'audio', wav: 'audio', m4a: 'audio', aac: 'audio',
                      flac: 'audio', ogg: 'audio',
                      mp4: 'video', mov: 'video', mkv: 'video', webm: 'video',
                      m4v: 'video', avi: 'video',
                      png: 'image', jpg: 'image', jpeg: 'image', webp: 'image',
                      bmp: 'image', gif: 'image',
                    }
                    const kind = tipos[ext]
                    if (!kind) {
                      toast('warn', 'Formato que eu não sei usar', file.name)
                      return
                    }
                    if (trackId === 'A1' && kind !== 'audio') {
                      toast('warn', 'O trilho de trilha só aceita áudio',
                        `${file.name} é ${kind}. Solte no trilho de sobreposição.`)
                      return
                    }
                    if (trackId === 'V2' && kind === 'audio') {
                      toast('warn', 'Sobreposição é imagem ou vídeo',
                        'Solte o áudio no trilho Trilha.')
                      return
                    }
                    toast('info', `Procurando ${file.name} no seu disco…`,
                      'O arquivo não sai do lugar — só preciso do caminho.')
                    try {
                      let caminho = (await api.locate(file.name, file.size)).path
                      if (!caminho) {
                        // não achou pelo nome: abre a janela do Windows já no
                        // tipo certo, em vez de largar o usuário na aba Mídia
                        toast('info', `Não achei "${file.name}" nas pastas de sempre`,
                          'Abrindo a janela do seu computador para você apontar.')
                        try {
                          const r = await api.escolher(
                            kind as 'video' | 'audio' | 'image',
                            kind === 'audio' ? 'Escolher a música' : 'Escolher o arquivo')
                          if (r.cancelado) return
                          caminho = r.path
                        } catch {
                          toast('warn', 'Aponte o arquivo pela aba Mídia',
                            'Esta máquina não conseguiu abrir a janela do sistema.')
                          setTab('midia')
                          return
                        }
                      }
                      // O modelo suporta UMA trilha (plan.music é um
                      // dicionário, não uma lista): soltar a segunda SUBSTITUI
                      // a primeira. Substituir é razoável — trocar de música é
                      // o gesto normal — mas em silêncio não é.
                      const jaTem = trackId === 'A1'
                        && !!project.plan?.music?.media_id
                      snapshot()
                      const m = await api.addMedia(project.id, caminho, kind)
                      const mid = m?.id ?? m?.media?.id
                      if (trackId === 'A1') {
                        const antes = project.plan?.music ?? {}
                        await api.setMusic(project.id, {
                          media_id: mid,
                          // trocar de música não devolve o volume e o ducking
                          // ao padrão: o que ele ajustou continua valendo
                          gain_db: antes.gain_db ?? -18,
                          ducking: antes.ducking ?? true,
                          duck_amount: antes.duck_amount ?? 12,
                          fade_in: antes.fade_in ?? 1, fade_out: antes.fade_out ?? 2,
                          muted: antes.muted ?? false, enabled: true,
                          out_start: antes.out_start ?? 0,
                          out_end: antes.out_end ?? (timeline?.duration ?? 0),
                        })
                        toast('ok', jaTem ? 'Trilha trocada' : 'Trilha no lugar',
                          jaTem
                            ? 'Só cabe uma trilha: a anterior saiu. O volume e o '
                              + 'ducking que você ajustou continuam valendo.'
                            : 'Já entra abaixando na fala. Arraste as bordas para '
                              + 'mudar onde ela toca, e o volume fica no painel '
                              + 'da direita.')
                      } else if (kind === 'image') {
                        await api.addOverlay(project.id, {
                          media_id: mid, out_start: getPlayhead(),
                          out_end: getPlayhead() + 3,
                          x: safeZone?.anchor?.x ?? 0.5, y: safeZone?.anchor?.y ?? 0.2,
                        })
                        toast('ok', 'Imagem sobreposta no ponto atual')
                      } else {
                        await api.addCutaway(project.id, {
                          media_id: mid, out_start: getPlayhead(),
                          out_end: Math.min(timeline?.duration ?? 0,
                                            getPlayhead() + 5),
                          media_start: 0,
                        })
                        toast('ok', 'Vídeo por cima no ponto atual',
                          'O seu áudio continua por baixo.')
                      }
                      await refresh()
                    } catch (e: any) {
                      toast('error', 'Não deu para usar esse arquivo',
                        String(e.message ?? e))
                    }
                  }}
                  onAddToTrack={async (trackId) => {
                    // o "+" do trilho abre a JANELA DO SISTEMA já no tipo certo
                    const kind = trackId === 'A1' ? 'audio' : 'video'
                    try {
                      const r = await api.escolher(kind as 'video' | 'audio',
                        trackId === 'A1' ? 'Escolher a música' : 'Escolher o vídeo')
                      if (r.cancelado) return
                      snapshot()
                      const m = await api.addMedia(project.id, r.path, kind)
                      const mid = m?.id ?? m?.media?.id
                      if (trackId === 'A1') {
                        const antes = project.plan?.music ?? {}
                        await api.setMusic(project.id, {
                          media_id: mid,
                          gain_db: antes.gain_db ?? -18,
                          ducking: antes.ducking ?? true,
                          duck_amount: antes.duck_amount ?? 12,
                          fade_in: antes.fade_in ?? 1, fade_out: antes.fade_out ?? 2,
                          muted: antes.muted ?? false, enabled: true,
                          out_start: 0, out_end: timeline?.duration ?? 0,
                        })
                        toast('ok', 'Música no trilho',
                          'Já entra abaixando na fala. O volume e o mudo ficam '
                          + 'no painel da direita.')
                      } else {
                        await api.addCutaway(project.id, {
                          media_id: mid, out_start: getPlayhead(),
                          out_end: Math.min(timeline?.duration ?? 0,
                                            getPlayhead() + 5),
                          media_start: 0,
                        })
                        toast('ok', 'Vídeo por cima no ponto atual')
                      }
                      await refresh()
                    } catch (e: any) {
                      toast('warn', 'Use a aba Mídia', String(e.message ?? e))
                      setTab(trackId === 'A1' ? 'midia' : 'midia')
                    }
                  }}
                  onResizeRemoved={async (a, b, na, nb) => {
                    snapshot()
                    try {
                      const r = await api.resizeRemoved(project.id, a, b, na, nb)
                      await refresh()
                      toast('ok', 'Trecho removido ajustado',
                        (r.explain ?? []).join('\n'))
                    } catch (e: any) {
                      toast('warn', 'Não deu para ajustar', String(e.message ?? e))
                    }
                  }}
                  onRestore={restore}
                  onToggleTake={async (id, restored) => {
                    snapshot()
                    await api.setTake(project.id, id, restored)
                    const job = await api.autoedit(project.id)
                    setState({ activeJob: job })
                  }}
                  onToggleClap={async (id, enabled) => {
                    // ligar uma palma cria (ou tira) um take descartado: sem
                    // refazer a edição a timeline mostrava a marca mudada e o
                    // corte antigo
                    snapshot()
                    await api.setClap(project.id, id, enabled)
                    const job = await api.autoedit(project.id)
                    setState({ activeJob: job })
                  }}
                  onSubtitleEdge={async (cueId, side, outTime) => {
                    snapshot()
                    await api.editSubtitle(project.id, cueId,
                      { [side]: Number(outTime.toFixed(3)) })
                    await refresh()
                  }} />
      )}
      </>}
    </div>
  )
}
