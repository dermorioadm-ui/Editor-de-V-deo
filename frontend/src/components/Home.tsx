import { useEffect, useRef, useState } from 'react'
import FileBrowser from './FileBrowser'
import { api } from '../lib/api'
import { bytes, timecode } from '../lib/format'
import { setState, toast, useStore } from '../state/store'

const VIDEO_EXT = ['.mp4', '.mov', '.mkv', '.m4v', '.avi', '.webm', '.mts', '.m2ts']

export default function Home() {
  const [health, setHealth] = useState<any>(null)
  const [presets, setPresets] = useState<any[]>([])
  const [projects, setProjects] = useState<any[]>([])
  const [preset, setPreset] = useState('VSL')
  const [path, setPath] = useState('')
  const [browsing, setBrowsing] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [locating, setLocating] = useState('')
  const dropRef = useRef<HTMLDivElement>(null)

  const refresh = () => api.projects().then(setProjects).catch(() => {})

  useEffect(() => {
    api.health().then(setHealth).catch(() => {})
    api.presets().then((p) => { setPresets(p); }).catch(() => {})
    refresh()
  }, [])

  async function openProject(id: string, andEdit = false) {
    setBusy(true)
    try {
      const project = await api.project(id)
      const env = project.analysis?.words?.length
        ? await api.envelope(id).catch(() => null)
        : null
      setState({
        view: 'editor', project, envelope: env,
        timeline: project.timeline ?? null,
        words: project.analysis?.words ?? [],
        removedWordIds: project.analysis?.removed_word_ids ?? [],
        fillers: project.analysis?.fillers ?? [],
        history: [], future: [], selection: null, selectedClip: null,
      })
      if (andEdit) {
        const job = await api.oneclick(id, preset)
        setState({ activeJob: job })
      }
    } catch (e: any) {
      toast('error', 'Não deu para abrir o projeto', String(e.message ?? e))
    } finally {
      setBusy(false)
    }
  }

  async function start(sourcePath: string) {
    if (!sourcePath) return
    setBusy(true)
    try {
      const project = await api.createProject(sourcePath, '', preset)
      await openProject(project.id, true)
    } catch (e: any) {
      toast('error', 'Não deu para criar o projeto', String(e.message ?? e))
      setBusy(false)
    }
  }

  async function onDrop(ev: React.DragEvent) {
    ev.preventDefault()
    setDragging(false)
    const file = ev.dataTransfer.files?.[0]
    if (!file) return
    setLocating(`procurando ${file.name} no seu disco…`)
    try {
      const res = await api.locate(file.name, file.size)
      setLocating('')
      if (res.path) {
        setPath(res.path)
        await start(res.path)
      } else {
        toast('warn', 'Não achei esse arquivo nas pastas conhecidas',
          `O navegador não entrega o caminho de um arquivo arrastado, e para não ` +
          `copiar ${bytes(file.size)} à toa eu procuro pelo nome. ` +
          `Use "Escolher no disco" e aponte o arquivo.`)
        setBrowsing(true)
      }
    } catch (e: any) {
      setLocating('')
      toast('error', 'Falha ao localizar o arquivo', String(e.message ?? e))
    }
  }

  const ffmpegOk = health?.ffmpeg?.ok
  const whisperOk = health?.faster_whisper

  return (
    <div className="flex-1 overflow-auto">
      <div className="max-w-5xl mx-auto px-6 py-10">
        <header className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight">Editor de Vídeo</h1>
          <p className="text-sm text-slate-400 mt-1">
            Joga o vídeo bruto aqui, escolhe o preset, aperta EDITAR.
            O arquivo não sai da sua máquina.
          </p>
        </header>

        {health && (!ffmpegOk || !whisperOk) && (
          <div className="card border-amber-800 bg-amber-950/40 p-4 mb-6 text-sm">
            <div className="font-medium text-amber-200 mb-1">Falta instalar alguma coisa</div>
            <ul className="text-amber-100/80 text-xs space-y-1 mt-2">
              {!ffmpegOk && (
                <li>• <b>ffmpeg</b> não foi encontrado. Sem ele nada funciona.
                  Veja a seção “Instalar o ffmpeg” do README.</li>
              )}
              {!whisperOk && (
                <li>• <b>faster-whisper</b> não está instalado.
                  Rode <code className="font-mono">pip install faster-whisper</code>.</li>
              )}
            </ul>
          </div>
        )}

        <div
          ref={dropRef}
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`card p-8 border-2 border-dashed transition
            ${dragging ? 'border-accent bg-accent/5' : 'border-line'}`}
        >
          <div className="text-center">
            <div className="text-4xl mb-3 opacity-40">⬇</div>
            <p className="text-base font-medium">Arraste o vídeo para cá</p>
            <p className="hint mt-1 max-w-lg mx-auto">
              O navegador não entrega o caminho do arquivo arrastado, então eu procuro
              pelo nome nas suas pastas de vídeo. Se não achar, é só apontar no disco —
              em nenhum dos dois casos o arquivo é copiado.
            </p>
            {locating && <p className="text-xs text-accent mt-2">{locating}</p>}
            <div className="flex items-center gap-2 justify-center mt-5">
              <input className="field max-w-md font-mono text-xs" value={path}
                     placeholder="C:\Users\voce\Videos\vsl.mp4"
                     onChange={(e) => setPath(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') start(path) }} />
              <button className="btn" onClick={() => setBrowsing(true)}>
                Escolher no disco
              </button>
            </div>
          </div>
        </div>

        <div className="mt-6 flex flex-wrap items-end gap-4">
          <div>
            <label className="label">Preset</label>
            <div className="flex gap-2">
              {presets.map((p) => (
                <button key={p.name}
                        className={`btn ${preset === p.name ? 'btn-primary' : ''}`}
                        title={p.description}
                        onClick={() => setPreset(p.name)}>
                  {p.name}
                </button>
              ))}
            </div>
          </div>
          <button className="btn btn-primary px-6 py-2.5 text-base ml-auto"
                  disabled={!path || busy || !ffmpegOk}
                  onClick={() => start(path)}>
            {busy ? 'abrindo…' : 'EDITAR'}
          </button>
        </div>
        <p className="hint mt-2">
          {presets.find((p) => p.name === preset)?.description}
        </p>

        {projects.length > 0 && (
          <section className="mt-10">
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wide mb-3">
              Projetos recentes
            </h2>
            <div className="card divide-y divide-line">
              {projects.map((p) => (
                <div key={p.id} className="px-4 py-2.5 flex items-center gap-3 text-sm">
                  <button className="flex-1 text-left min-w-0"
                          onClick={() => openProject(p.id)}>
                    <div className="font-medium truncate">{p.name}</div>
                    <div className="text-xs text-slate-500 truncate font-mono">
                      {p.source_path}
                    </div>
                  </button>
                  <span className="chip border-line text-slate-400">{p.preset}</span>
                  <span className="chip border-line text-slate-400">{p.status}</span>
                  <span className="text-xs text-slate-500 w-16 text-right">
                    {timecode(p.duration)}
                  </span>
                  <button className="btn btn-xs btn-danger"
                          onClick={async () => {
                            await api.deleteProject(p.id); refresh()
                          }}>
                    apagar
                  </button>
                </div>
              ))}
            </div>
          </section>
        )}

        {health && (
          <footer className="mt-10 text-[11px] text-slate-600 font-mono space-y-0.5">
            <div>ffmpeg: {ffmpegOk ? health.ffmpeg.detail : 'não encontrado'}</div>
            <div>
              transcrição: {whisperOk ? 'faster-whisper' : 'não instalado'}
              {' · '}modelo {health.whisper_model}
              {' · '}{health.device?.device}/{health.device?.compute_type}
              {health.device?.detail ? ` — ${health.device.detail}` : ''}
            </div>
            {health.hw_encoders?.length > 0 && (
              <div>encoders de GPU disponíveis: {health.hw_encoders.join(', ')}</div>
            )}
          </footer>
        )}
      </div>

      {browsing && (
        <FileBrowser extensions={VIDEO_EXT} title="Escolher o vídeo"
                     onClose={() => setBrowsing(false)}
                     onPick={(p) => { setBrowsing(false); setPath(p); start(p) }} />
      )}
    </div>
  )
}
