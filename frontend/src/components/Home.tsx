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
  // A CHAVE MORA AQUI, na primeira tela. Antes ela só existia na aba IA —
  // dentro do editor, que só abre DEPOIS do processamento, que é justamente
  // quem precisa dela. Ovo e galinha: a etapa da IA era pulada em silêncio na
  // primeira vez de todo mundo.
  const [ia, setIa] = useState<any>(null)
  const [chave, setChave] = useState('')
  const [salvandoChave, setSalvandoChave] = useState(false)
  const dropRef = useRef<HTMLDivElement>(null)

  const refresh = () => api.projects().then(setProjects).catch(() => {})

  useEffect(() => {
    api.health().then(setHealth).catch(() => {})
    api.aiConfig().then(setIa).catch(() => {})
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

  /** Abre a JANELA DO SISTEMA — a de verdade, a mesma de qualquer programa.
   *  O explorador escrito em HTML continua existindo, mas só como recuo para
   *  a máquina onde a janela não abre. Ninguém quer aprender um explorador de
   *  arquivos novo para abrir um MP4. */
  /** Guarda e TESTA a chave na hora: um erro de chave tem que aparecer aqui,
   *  com o campo na frente, e não no meio do processamento de um vídeo de
   *  2 GB — que é onde ele aparecia (e nem aparecia: era pulo em silêncio). */
  async function salvarChave() {
    const k = chave.trim()
    if (!k) return
    setSalvandoChave(true)
    try {
      await api.setAiConfig({ chave: k, cortes: true })
      const r = await api.testAi()
      setIa(await api.aiConfig())
      setChave('')
      toast('ok', 'Chave guardada e testada', `vai usar ${r.modelo}`)
    } catch (e: any) {
      await api.setAiConfig({ chave: '' })
      setIa(await api.aiConfig())
      toast('error', 'A chave não passou', String(e.message ?? e))
    } finally { setSalvandoChave(false) }
  }

  async function escolherNoDisco() {
    try {
      const r = await api.escolher('video', 'Escolher o vídeo para editar')
      if (r.cancelado) return
      setPath(r.path)
      await start(r.path)
    } catch (e: any) {
      // 501 = esta máquina não tem como abrir a janela do sistema
      setBrowsing(true)
      if (!String(e.message ?? '').includes('janela')) {
        toast('warn', 'Abri o explorador de dentro do app',
          String(e.message ?? e))
      }
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
        toast('info', `Não achei "${file.name}" nas pastas de sempre`,
          `Abrindo a janela do Windows para você apontar. Nada é copiado — ` +
          `eu só preciso do caminho.`)
        await escolherNoDisco()
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

        {/* A IA decide os cortes. Sem a chave, o editor cai na regra
            determinística — e o usuário TEM que saber disso antes de soltar
            o arquivo, não depois. */}
        <div className={`card p-3 mb-3 flex items-center gap-3
          ${ia?.tem_chave ? 'border-emerald-900/50 bg-emerald-950/15'
            : 'border-amber-900/50 bg-amber-950/15'}`}>
          <span className={`text-lg leading-none
            ${ia?.tem_chave ? 'text-emerald-400' : 'text-amber-400'}`}>
            {ia?.tem_chave ? '✓' : '!'}
          </span>
          {ia?.tem_chave ? (
            <>
              <div className="min-w-0">
                <p className="text-sm text-slate-200">
                  A IA vai cortar este vídeo
                </p>
                <p className="text-[11px] text-slate-500">
                  chave …{ia.final} · o vídeo não sai da máquina, só o texto
                </p>
              </div>
              <button className="btn btn-xs ml-auto"
                      onClick={async () => setIa(await api.setAiConfig({ chave: '' }))}>
                trocar
              </button>
            </>
          ) : (
            <>
              <div className="min-w-0 shrink-0">
                <p className="text-sm text-slate-200">
                  Cole a chave do Gemini para a IA cortar
                </p>
                <p className="text-[11px] text-slate-500">
                  uma vez só, fica guardada · sem ela o corte é só pela regra
                </p>
              </div>
              <input className="field flex-1 font-mono text-xs" type="password"
                     placeholder="AIza…" value={chave}
                     onChange={(e) => setChave(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') salvarChave() }} />
              <button className="btn btn-primary btn-xs shrink-0"
                      disabled={!chave.trim() || salvandoChave}
                      onClick={salvarChave}>
                {salvandoChave ? 'testando…' : 'guardar'}
              </button>
            </>
          )}
        </div>

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
              Ou clique em <b>Escolher no computador</b> e use a janela de sempre.
              Nos dois casos o arquivo <b>não é copiado nem enviado</b>: o editor
              lê ele direto de onde já está.
            </p>
            {locating && <p className="text-xs text-accent mt-2">{locating}</p>}
            <div className="flex items-center gap-2 justify-center mt-5">
              <input className="field max-w-md font-mono text-xs" value={path}
                     placeholder="C:\Users\voce\Videos\vsl.mp4"
                     onChange={(e) => setPath(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') start(path) }} />
              <button className="btn btn-primary" onClick={escolherNoDisco}>
                Escolher no computador…
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
