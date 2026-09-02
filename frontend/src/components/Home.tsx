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
  // Os dois ajustes que decidem o RITMO, e por isso valem ANTES de gerar:
  // refazer a edição depois custa uma volta inteira do pipeline.
  const [velocidade, setVelocidade] = useState(1.0)   // multiplicador global
  const [zoomForca, setZoomForca] = useState(1.0)     // intensidade da escada
  const [modelos, setModelos] = useState<any[]>([])
  const [modelo, setModelo] = useState('')
  const [looks, setLooks] = useState<any[]>([])
  const [look, setLook] = useState('nenhum')
  const [musica, setMusica] = useState('')            // caminho do MP3
  const [musicaVol, setMusicaVol] = useState(-18)
  // A legenda já sai no tamanho certo do formato sozinha (o preset é medido
  // numa altura de 1024 e reescalado para a altura real na criação do
  // projeto: 35 vira 66 num 1080x1920). Este seletor é só o empurrãozinho
  // para quem quer maior ou menor — em porcentagem do tamanho certo, nunca
  // um número cru de pixels que muda de significado a cada resolução.
  const [legenda, setLegenda] = useState(1.0)
  const [resolucao, setResolucao] = useState('source')
  // 30 fps por padrão. Medido num 1920x1080 a 60 fps: baixar a saída para 30
  // corta 34% do trabalho de renderização inteiro, e num vídeo de alguém
  // falando para a câmera 60 fps não acrescenta nada — é o padrão de anúncio
  // no Facebook, Instagram e YouTube.
  const [fpsSaida, setFpsSaida] = useState(30)
  const [iaCortes, setIaCortes] = useState(true)
  // formatos EXTRAS do mesmo corte — o principal é sempre a proporção da
  // gravação. Cada extra é uma geração de encode a mais, a partir da fonte.
  const [extras, setExtras] = useState<string[]>([])
  const [fonteInfo, setFonteInfo] = useState<any>(null)   // o que é o arquivo
  // MATERIAL AUXILIAR: gravações de tela, prints, imagens. Cada um com uma
  // linha dizendo o que é e onde deve entrar — é essa linha que a IA lê para
  // posicionar. Um quadro de 360 px mostra o que a imagem MOSTRA; só você
  // sabe a intenção.
  const [auxiliares, setAuxiliares] =
    useState<{ path: string; descricao: string }[]>([])
  const [cartoes, setCartoes] = useState(true)
  const [saida, setSaida] = useState<any>(null)      // pasta do vídeo pronto
  const [trocandoPasta, setTrocandoPasta] = useState(false)
  const dropRef = useRef<HTMLDivElement>(null)

  // Descobre a proporção da gravação assim que o arquivo é carregado. Sem
  // isso a tela oferecia "quadrado e horizontal" fixo, e quem gravava em
  // horizontal não tinha como pedir o vertical — que é justamente o formato
  // que ele mais usa.
  useEffect(() => {
    if (!path) { setFonteInfo(null); setExtras([]); return }
    let vivo = true
    api.probe(path).then((r) => {
      if (!vivo) return
      setFonteInfo(r)
      setExtras((v) => v.filter((x) => x !== r.formato))
    }).catch(() => { if (vivo) setFonteInfo(null) })
    return () => { vivo = false }
  }, [path])

  const FORMATOS: Record<string, string> = {
    '9:16': 'vertical (Reels, TikTok)',
    '1:1': 'quadrado (feed)',
    '16:9': 'horizontal (YouTube)',
  }
  const extrasPossiveis = Object.keys(FORMATOS)
    .filter((f) => f !== (fonteInfo?.formato ?? '9:16'))

  const iaLiga = Boolean(ia?.tem_chave) && iaCortes
  // Tem chave mas o modelo ainda não foi escrito no servidor: gerar agora
  // sairia com o modelo que o programa escolheu sozinho. Segura o botão —
  // é o instante entre colar a chave e a lista responder, não um estado
  // em que se fica.
  const iaPendente = Boolean(ia?.tem_chave) && !ia?.modelo_fixado

  const refresh = () => api.projects().then(setProjects).catch(() => {})

  /** Lê a config da IA e, se houver chave, a lista de modelos que ela alcança.
   *
   *  O modelo TEM que ficar decidido e escrito antes de o vídeo rodar. O furo
   *  era aqui: quem colava a chave agora só via a lista carregar na próxima
   *  vez que abrisse o programa — nesta visita `modelo` continuava vazio, nada
   *  era gravado, e o servidor resolvia o vazio sozinho caindo no primeiro da
   *  lista de preferência. O usuário escolhia um modelo e o vídeo saía de
   *  outro. */
  async function lerIa() {
    const c = await api.aiConfig()
    setIa(c)
    setModelo(c.modelo || '')
    setIaCortes(c.cortes !== false)
    if (!c.tem_chave) { setModelos([]); return c }
    try {
      const r = await api.aiModelos()
      setModelos(r.modelos ?? [])
      if (!c.modelo && r.padrao) {
        // ninguém escolheu ainda: FIXA o padrão no servidor agora, não na
        // hora de gerar — "antes de usar" é aqui
        const c2 = await api.setAiConfig({ modelo: r.padrao })
        setIa(c2)
        setModelo(c2.modelo || r.padrao)
        return c2
      }
    } catch { /* sem rede a lista fica vazia; o modelo escrito continua valendo */ }
    return c
  }

  useEffect(() => {
    api.health().then(setHealth).catch(() => {})
    lerIa().catch(() => {})
    api.looks().then(setLooks).catch(() => {})
    api.outputDir().then(setSaida).catch(() => {})
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
        const job = await api.oneclick(id, preset, receita())
        setState({ activeJob: job })
      }
    } catch (e: any) {
      toast('error', 'Não deu para abrir o projeto', String(e.message ?? e))
    } finally {
      setBusy(false)
    }
  }

  /** TODA a receita num objeto só, mandada JUNTO com o clique único.
   *
   *  Antes ela era gravada por /params logo antes de disparar o processamento
   *  — e o processamento reaplicava o preset por cima, reconstruindo
   *  velocidade, zoom, legenda e exportação do zero. Os sliders existiam e
   *  não mudavam nada no arquivo. Mandando junto, o servidor aplica a receita
   *  DEPOIS do preset e a ordem para de depender de quem gravou primeiro. */
  function receita() {
    return {
      speed: { global_multiplier: velocidade },
      zoom: { intensity: zoomForca },
      style: { fontsize_scale: legenda },
      export: { scale: resolucao, extras, fps: fpsSaida },
      look: look || 'nenhum',
    }
  }

  async function start(sourcePath: string) {
    if (!sourcePath) return
    setBusy(true)
    try {
      const project = await api.createProject(sourcePath, '', preset)
      // O modelo NÃO é gravado aqui: ele já foi fixado quando a chave foi
      // guardada ou quando o seletor mudou. Gravar na hora de gerar era o
      // furo — se a gravação falhasse, o vídeo saía com outro modelo e
      // ninguém ficava sabendo.
      if (iaCortes !== (ia?.cortes !== false)) {
        await api.setAiConfig({ cortes: iaCortes }).catch(() => {})
      }
      // o material auxiliar sobe ANTES do clique único: é lá dentro que a IA
      // decide em que trecho cada um entra
      for (const aux of auxiliares) {
        try {
          const ext = aux.path.split('.').pop()?.toLowerCase() ?? ''
          const kind = ['png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif']
            .includes(ext) ? 'image' : 'video'
          await api.addMedia(project.id, aux.path, kind, aux.descricao)
        } catch { /* um anexo que não sobe não pode impedir o vídeo */ }
      }
      if (cartoes !== (ia?.cartoes !== false)) {
        await api.setAiConfig({ cartoes }).catch(() => {})
      }
      if (musica) {
        try {
          const m = await api.addMedia(project.id, musica, 'audio')
          await api.setMusic(project.id, {
            media_id: m?.id ?? m?.media?.id, gain_db: musicaVol,
            ducking: true, duck_amount: 12, fade_in: 1, fade_out: 2,
            muted: false, enabled: true, out_start: 0, out_end: 0,
          })
        } catch { /* sem trilha o vídeo sai igual */ }
      }
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
      await api.testAi()
      // a mesma ação que guarda a chave DECIDE o modelo e o escreve
      const c = await lerIa()
      setChave('')
      toast('ok', 'Chave guardada, testada e modelo fixado',
        `vai usar ${c.modelo}`)
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
      // SÓ carrega o caminho. Quem dispara é o botão GERAR, depois de você
      // ver a receita. Disparar aqui era o furo que anulava a tela inteira.
      setPath(r.path)
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
          <div className="flex items-center gap-3">
            <img src="/logo-mark.svg" alt="" className="w-9 h-9 shrink-0" />
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Shark Play</h1>
              <p className="text-xs text-slate-500 -mt-0.5">editor de vídeo local</p>
            </div>
          </div>
          <p className="text-sm text-slate-400 mt-3">
            Monta a receita aqui embaixo e aperta uma vez. O vídeo volta
            cortado, acelerado, com câmera, filtro e legenda — pronto para
            baixar. O arquivo não sai da sua máquina.
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
          ${iaLiga ? 'border-emerald-900/50 bg-emerald-950/15'
            : 'border-amber-900/50 bg-amber-950/15'}`}>
          <span className={`text-lg leading-none
            ${iaLiga ? 'text-emerald-400' : 'text-amber-400'}`}>
            {iaLiga ? '✓' : '!'}
          </span>
          {ia?.tem_chave ? (
            <>
              <div className="min-w-0">
                <p className="text-sm text-slate-200">
                  {iaLiga ? 'A IA vai cortar este vídeo'
                    : 'A IA está desligada — o corte vai ser só pela regra'}
                </p>
                <p className="text-[11px] text-slate-500">
                  chave …{ia.final} ·{' '}
                  {ia.modelo_fixado
                    ? <span className="font-mono text-slate-400">{ia.modelo}</span>
                    : 'escolhendo o modelo…'}
                  {' '}· o vídeo não sai da máquina, só o texto
                </p>
              </div>
              {/* O selo tinha que refletir o interruptor, não só a chave: quem
                  desligou a IA uma vez lá dentro via "a IA vai cortar" em
                  verde para sempre. */}
              <label className="ml-auto flex items-center gap-1.5 text-xs
                                text-slate-400 cursor-pointer">
                <input type="checkbox" checked={iaCortes}
                       onChange={(e) => setIaCortes(e.target.checked)} />
                a IA decide os cortes
              </label>
              <button className="btn btn-xs"
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
            {path ? (
              <>
                <div className="text-3xl mb-2">🎬</div>
                <p className="text-base font-medium text-emerald-300">
                  Vídeo carregado — <b>ainda não comecei</b>
                </p>
                {fonteInfo && (
                  <p className="text-xs text-slate-400 mt-1 font-mono">
                    {fonteInfo.width}×{fonteInfo.height} ·{' '}
                    {FORMATOS[fonteInfo.formato]} ·{' '}
                    {Math.round(fonteInfo.fps)} fps ·{' '}
                    {Math.floor(fonteInfo.duration / 60)}:
                    {String(Math.round(fonteInfo.duration % 60)).padStart(2, '0')}
                  </p>
                )}
                <p className="hint mt-1 max-w-xl mx-auto">
                  Confira a receita aqui embaixo (modelo da IA, filtro,
                  velocidade, zoom, legenda, formatos) e aperte
                  <b> GERAR VÍDEO PRONTO</b>. Nada roda até você apertar.
                </p>
              </>
            ) : (
              <>
                <div className="text-4xl mb-3 opacity-40">⬇</div>
                <p className="text-base font-medium">Arraste o vídeo para cá</p>
                <p className="hint mt-1 max-w-lg mx-auto">
                  Ou clique em <b>Escolher no computador</b> e use a janela de
                  sempre. Nos dois casos o arquivo <b>não é copiado nem
                  enviado</b>: o editor lê ele direto de onde já está.
                </p>
              </>
            )}
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
          {ia?.tem_chave && (
            <div className="w-52">
              <label className="label">Modelo da IA</label>
              <select className="field w-full py-1.5 text-xs font-mono"
                      value={modelo}
                      onChange={async (e) => {
                        const id = e.target.value
                        setModelo(id)
                        // grava JÁ. Guardar para a hora de gerar era o mesmo
                        // erro de sempre: escolha que só vale depois não vale.
                        try { setIa(await api.setAiConfig({ modelo: id })) }
                        catch (err: any) {
                          toast('error', 'Esse modelo não deu',
                            String(err.message ?? err))
                          await lerIa()
                        }
                      }}>
                {!modelos.length && <option value={modelo}>{modelo || '…'}</option>}
                {modelos.map((m) => (
                  <option key={m.id} value={m.id}>{m.id}</option>
                ))}
              </select>
              <p className="text-[10px] text-slate-600 leading-tight">
                {ia?.modelo_fixado ? `fixado: ${ia.modelo}` : 'ainda não fixado'}
              </p>
            </div>
          )}

          <div className="w-40">
            <label className="label">Filtro</label>
            <select className="field w-full py-1.5 text-xs"
                    value={look} onChange={(e) => setLook(e.target.value)}>
              {looks.map((l: any) => (
                <option key={l.id} value={l.id}>{l.name ?? l.label ?? l.id}</option>
              ))}
            </select>
            <p className="text-[10px] text-slate-600 leading-tight">
              já sai queimado no arquivo
            </p>
          </div>

          <div className="w-52">
            <label className="label flex justify-between">
              <span>Música de fundo</span>
              {musica && (
                <button className="text-[10px] text-slate-500 hover:text-slate-300"
                        onClick={() => setMusica('')}>tirar</button>
              )}
            </label>
            {musica ? (
              <>
                <div className="field w-full py-1.5 text-xs truncate"
                     title={musica}>{musica.split(/[\\/]/).pop()}</div>
                <input type="range" min={-30} max={-6} step={1} className="w-full mt-1"
                       value={musicaVol}
                       onChange={(e) => setMusicaVol(+e.target.value)} />
                <p className="text-[10px] text-slate-600 leading-tight">
                  volume {musicaVol} dB · abaixa sozinha na fala
                </p>
              </>
            ) : (
              <>
                <button className="btn w-full py-1.5 text-xs"
                        onClick={async () => {
                          try {
                            const r = await api.escolher('audio', 'Escolher a música')
                            if (!r.cancelado) setMusica(r.path)
                          } catch {
                            toast('warn', 'Use a aba Mídia depois de gerar',
                              'Esta máquina não abriu a janela do sistema.')
                          }
                        }}>
                  escolher MP3…
                </button>
                <p className="text-[10px] text-slate-600 leading-tight">
                  opcional
                </p>
              </>
            )}
          </div>

          <div className="w-40">
            <label className="label">Legenda</label>
            <select className="field w-full py-1.5 text-xs"
                    value={legenda} onChange={(e) => setLegenda(+e.target.value)}>
              <option value={0.8}>menor</option>
              <option value={1}>do formato</option>
              <option value={1.2}>maior</option>
              <option value={1.45}>bem maior</option>
            </select>
            <p className="text-[10px] text-slate-600 leading-tight">
              já sai no tamanho certo do vertical
            </p>
          </div>

          <div className="w-44">
            <label className="label">Formatos extras</label>
            <div className="space-y-0.5 pt-0.5">
              {extrasPossiveis.map((id) => (
                <label key={id} className="flex items-center gap-1.5 text-xs
                                           text-slate-300 cursor-pointer">
                  <input type="checkbox" checked={extras.includes(id)}
                         onChange={(e) => setExtras((v) => e.target.checked
                           ? [...v, id] : v.filter((x) => x !== id))} />
                  {FORMATOS[id]}
                </label>
              ))}
            </div>
            <p className="text-[10px] text-slate-600 leading-tight">
              {fonteInfo
                ? `além do ${FORMATOS[fonteInfo.formato]?.split(' ')[0]}, `
                  + 'recortados no rosto'
                : 'recortados no rosto, sem distorcer'}
            </p>
          </div>

          <div className="w-36">
            <label className="label">Quadros/s</label>
            <select className="field w-full py-1.5 text-xs"
                    value={fpsSaida}
                    onChange={(e) => setFpsSaida(+e.target.value)}>
              <option value={30}>30 (metade do tempo)</option>
              <option value={0}>os da gravação</option>
              <option value={24}>24 (cinema)</option>
            </select>
            <p className="text-[10px] text-slate-600 leading-tight">
              30 é o padrão de anúncio
            </p>
          </div>

          <div className="w-40">
            <label className="label">Resolução</label>
            <select className="field w-full py-1.5 text-xs"
                    value={resolucao} onChange={(e) => setResolucao(e.target.value)}>
              <option value="source">a da gravação</option>
              <option value="1080">1080 de largura</option>
              <option value="720">720 de largura</option>
            </select>
            <p className="text-[10px] text-slate-600 leading-tight">
              uma geração de encode só
            </p>
          </div>

          <div className="w-44">
            <label className="label flex justify-between">
              <span>Velocidade</span>
              <span className="font-mono text-slate-300">
                {velocidade === 1 ? 'a da etapa' : `+${((velocidade - 1) * 100).toFixed(0)}%`}
              </span>
            </label>
            <input type="range" min={1} max={1.3} step={0.05} className="w-full"
                   value={velocidade}
                   onChange={(e) => setVelocidade(+e.target.value)} />
            <p className="text-[10px] text-slate-600 leading-tight">
              multiplica o que cada etapa já pede — 0% deixa só a da etapa
            </p>
          </div>

          <div className="w-44">
            <label className="label flex justify-between">
              <span>Zoom entre cenas</span>
              <span className="font-mono text-slate-300">
                {zoomForca < 0.6 ? 'quase parado'
                  : zoomForca < 1.05 ? 'do preset'
                  : zoomForca < 1.6 ? 'marcado' : 'agressivo'}
              </span>
            </label>
            <input type="range" min={0} max={2} step={0.1} className="w-full"
                   value={zoomForca}
                   onChange={(e) => setZoomForca(+e.target.value)} />
            <p className="text-[10px] text-slate-600 leading-tight">
              o quanto o enquadramento muda a cada corte
            </p>
          </div>

          <button className="btn btn-primary px-7 py-3 text-base ml-auto"
                  disabled={!path || busy || !ffmpegOk || iaPendente}
                  title={iaPendente ? 'escolhendo o modelo da IA…' : ''}
                  onClick={() => start(path)}>
            {busy ? 'começando…'
              : iaPendente ? 'fixando o modelo…' : 'GERAR VÍDEO PRONTO'}
          </button>
        </div>
        {/* MATERIAL AUXILIAR. A IA lê a sua linha de descrição junto com um
            quadro de cada arquivo e decide em que trecho da SUA fala ele
            entra. Vídeo cobre a imagem (a sua voz continua por baixo);
            imagem aparece por cima. */}
        <div className="card p-3 mt-4">
          <div className="flex items-center gap-3 flex-wrap">
            <div>
              <div className="text-sm text-slate-200 font-medium">
                Material auxiliar
              </div>
              <p className="text-[11px] text-slate-500">
                gravações de tela, prints, imagens — a IA põe cada um no
                trecho em que você fala daquilo
              </p>
            </div>
            <button className="btn btn-xs ml-auto"
                    onClick={async () => {
                      try {
                        const r = await api.escolher('media',
                          'Escolher material auxiliar')
                        if (!r.cancelado) {
                          setAuxiliares((v) => [...v,
                            { path: r.path, descricao: '' }])
                        }
                      } catch {
                        toast('warn', 'Use a aba Mídia depois de gerar',
                          'Esta máquina não abriu a janela do sistema.')
                      }
                    }}>
              + anexar arquivo
            </button>
            <label className="flex items-center gap-1.5 text-xs text-slate-400
                              cursor-pointer">
              <input type="checkbox" checked={cartoes}
                     onChange={(e) => setCartoes(e.target.checked)} />
              cartões de tópico
            </label>
          </div>
          {auxiliares.length > 0 && (
            <div className="mt-2 space-y-1.5">
              {auxiliares.map((aux, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="text-[11px] text-slate-500 font-mono w-40
                                   truncate" title={aux.path}>
                    {aux.path.split(/[\\/]/).pop()}
                  </span>
                  <input className="field flex-1 py-1 text-xs"
                         placeholder="o que é e onde entra — ex.: tela de cadastro, quando eu falo do passo 1"
                         value={aux.descricao}
                         onChange={(e) => setAuxiliares((v) => v.map((a, k) =>
                           k === i ? { ...a, descricao: e.target.value } : a))} />
                  <button className="text-[11px] text-slate-500 hover:text-slate-300"
                          onClick={() => setAuxiliares((v) =>
                            v.filter((_, k) => k !== i))}>tirar</button>
                </div>
              ))}
              <p className="text-[10px] text-slate-600">
                Sem a descrição a IA só vê a imagem e chuta. Uma linha basta.
              </p>
            </div>
          )}
        </div>

        <p className="hint mt-2">
          {presets.find((p) => p.name === preset)?.description}
        </p>
        {/* Onde o arquivo pronto cai. Ficava só na aba Exportar — que abre
            DEPOIS de o arquivo já ter sido escrito na pasta antiga. */}
        {saida && (
          <p className="hint mt-1">
            O vídeo pronto vai para <code className="font-mono text-slate-400">
            {saida.path}</code>{' '}
            <button className="text-slate-500 hover:text-slate-300 underline"
                    onClick={() => setTrocandoPasta((v) => !v)}>trocar</button>
          </p>
        )}
        {trocandoPasta && (
          <div className="flex gap-1.5 mt-2 max-w-xl">
            <input className="field py-1 text-xs flex-1"
                   defaultValue={saida?.path ?? ''}
                   placeholder="cole o caminho da pasta e aperte Enter"
                   onKeyDown={async (e) => {
                     if (e.key !== 'Enter') return
                     try {
                       const r = await api.setOutputDir(
                         (e.target as HTMLInputElement).value)
                       setSaida({ ...saida, path: r.path })
                       setTrocandoPasta(false)
                       toast('ok', 'Pasta de saída trocada', r.path)
                     } catch (err: any) {
                       toast('error', 'Pasta inválida', String(err.message ?? err))
                     }
                   }} />
            <button className="btn btn-xs"
                    onClick={async () => {
                      const r = await api.setOutputDir('')
                      setSaida({ ...saida, path: r.path })
                      setTrocandoPasta(false)
                    }}>voltar ao padrão</button>
          </div>
        )}

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
