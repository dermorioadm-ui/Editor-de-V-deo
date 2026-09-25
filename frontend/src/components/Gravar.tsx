import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { toast } from '../state/store'

/**
 * O AMBIENTE DE GRAVAÇÃO.
 *
 * Grava pela câmera e pelo microfone da máquina e guarda a tomada na esteira.
 * O arquivo vai para o disco pelo 127.0.0.1 — o navegador é o desta máquina e o
 * servidor também. O navegador não tem permissão para escrever no disco
 * sozinho: esse é o único caminho que existe, e ele não sai do computador.
 *
 * As tomadas ficam numa lista. A que não prestou se apaga; as que prestaram vão
 * juntas para a esteira, e daí em diante é o mesmo caminho de sempre — corte de
 * silêncio em cada uma, montagem, legenda, um arquivo.
 */

type Tomada = {
  nome: string; path: string; duracao: number; size_bytes: number
  largura: number; altura: number; tem_audio: boolean; criado_em: number
}

// Na ordem de preferência. O MP4 é o que qualquer programa abre; o Chrome só
// ganhou o muxer dele há pouco, então o WebM continua sendo a rede de
// segurança. O servidor reempacota os dois de qualquer jeito — o que ele
// precisa é de um arquivo com duração no cabeçalho, e o MediaRecorder não
// escreve isso enquanto está gravando.
const FORMATOS = [
  'video/mp4;codecs=avc1.42E01E,mp4a.40.2',
  'video/mp4',
  'video/webm;codecs=vp9,opus',
  'video/webm;codecs=vp8,opus',
  'video/webm',
]
const FORMATOS_AUDIO = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm']

function melhorFormato(somenteAudio: boolean): string {
  const lista = somenteAudio ? FORMATOS_AUDIO : FORMATOS
  for (const f of lista) {
    try {
      if (MediaRecorder.isTypeSupported(f)) return f
    } catch { /* navegador sem MediaRecorder: o aviso vem de fora */ }
  }
  return ''
}

function tempo(s: number): string {
  const m = Math.floor(s / 60)
  const r = Math.floor(s % 60)
  return `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
}

function tamanho(b: number): string {
  return b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.round(b / 1024)} KB`
}

interface Props {
  onFechar: () => void
  /** manda as tomadas escolhidas para a primeira tela, na ordem da lista */
  onUsar: (paths: string[]) => void
}

export default function Gravar({ onFechar, onUsar }: Props) {
  const video = useRef<HTMLVideoElement | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const rec = useRef<MediaRecorder | null>(null)
  const pedacos = useRef<Blob[]>([])
  const [dispositivos, setDispositivos] = useState<MediaDeviceInfo[]>([])
  const [camera, setCamera] = useState('')
  const [microfone, setMicrofone] = useState('')
  const [somenteAudio, setSomenteAudio] = useState(false)
  const [gravando, setGravando] = useState(false)
  const [contagem, setContagem] = useState(0)
  const [segundos, setSegundos] = useState(0)
  const [salvando, setSalvando] = useState(false)
  const [tomadas, setTomadas] = useState<Tomada[]>([])
  const [escolhidas, setEscolhidas] = useState<string[]>([])
  const [erro, setErro] = useState('')

  // --------------------------------------------------------- teleprompter
  const [texto, setTexto] = useState('')
  const [rolando, setRolando] = useState(false)
  const [velocidade, setVelocidade] = useState(40)   // pixels por segundo
  const [corpo, setCorpo] = useState(34)             // tamanho da letra
  const [espelhado, setEspelhado] = useState(false)
  const [mostrarTexto, setMostrarTexto] = useState(true)
  const prompter = useRef<HTMLDivElement | null>(null)

  const listar = useCallback(async () => {
    try { setTomadas(await api.gravacoes()) } catch { /* lista vazia é ok */ }
  }, [])

  // ---------------------------------------------------------- a câmera
  const abrirCamera = useCallback(async () => {
    setErro('')
    // CONTEXTO SEGURO. O navegador só entrega câmera e microfone em https ou
    // em 127.0.0.1/localhost. Pelo endereço de rede (o iniciar-rede.bat, que
    // existe para revisar do celular) o pedido é NEGADO pelo navegador antes
    // de chegar aqui, e a mensagem crua dele não diz o porquê — o usuário fica
    // achando que negou a permissão sem querer.
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setErro('Este navegador não libera a câmera neste endereço. Grave com o '
        + 'editor aberto em http://127.0.0.1:8000 (o iniciar.bat), não pelo '
        + 'endereço de rede — a regra é do navegador, não do editor.')
      return
    }
    try {
      stream.current?.getTracks().forEach((t) => t.stop())
      const pedido: MediaStreamConstraints = {
        audio: microfone ? { deviceId: { exact: microfone } } : true,
        video: somenteAudio ? false
          : (camera ? { deviceId: { exact: camera }, width: { ideal: 1920 },
                        height: { ideal: 1080 } }
                    : { width: { ideal: 1920 }, height: { ideal: 1080 } }),
      }
      const s = await navigator.mediaDevices.getUserMedia(pedido)
      stream.current = s
      if (video.current) video.current.srcObject = s
      // Os nomes dos aparelhos só aparecem DEPOIS da permissão: antes dela o
      // navegador devolve a lista com os rótulos em branco, de propósito, para
      // um site não conseguir saber quais câmeras você tem sem pedir.
      setDispositivos(await navigator.mediaDevices.enumerateDevices())
    } catch (e: any) {
      setErro(String(e?.message ?? e))
    }
  }, [camera, microfone, somenteAudio])

  useEffect(() => { abrirCamera(); listar() }, [abrirCamera, listar])

  // A CÂMERA TEM QUE APAGAR AO SAIR. Sem isto a luz ao lado da lente fica
  // acesa depois de fechar a tela, e o usuário fica achando que está sendo
  // gravado — porque, tecnicamente, o aparelho continua aberto.
  useEffect(() => () => {
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
  }, [])

  // ------------------------------------------------------ o relógio
  useEffect(() => {
    if (!gravando) return
    const id = window.setInterval(() => setSegundos((s) => s + 1), 1000)
    return () => window.clearInterval(id)
  }, [gravando])

  // ------------------------------------------- o texto que rola sozinho
  useEffect(() => {
    if (!rolando) return
    let pedido = 0
    let anterior = performance.now()
    const passo = (agora: number) => {
      const dt = (agora - anterior) / 1000
      anterior = agora
      const el = prompter.current
      if (el) {
        el.scrollTop += velocidade * dt
        // chegou ao fim: para sozinho, em vez de ficar raspando o fundo
        if (el.scrollTop + el.clientHeight >= el.scrollHeight - 2) setRolando(false)
      }
      pedido = requestAnimationFrame(passo)
    }
    pedido = requestAnimationFrame(passo)
    return () => cancelAnimationFrame(pedido)
  }, [rolando, velocidade])

  // ---------------------------------------------------------- gravar
  const comecarDeVerdade = () => {
    const s = stream.current
    if (!s) { setErro('a câmera não está aberta'); return }
    const mime = melhorFormato(somenteAudio)
    if (!mime) { setErro('este navegador não sabe gravar vídeo'); return }
    pedacos.current = []
    const r = new MediaRecorder(s, { mimeType: mime })
    r.ondataavailable = (e) => { if (e.data.size) pedacos.current.push(e.data) }
    r.onstop = async () => {
      const blob = new Blob(pedacos.current, { type: mime })
      pedacos.current = []
      setSalvando(true)
      try {
        const carimbo = new Date().toLocaleString('pt-BR').replace(/[/:]/g, '-')
        const ext = mime.includes('mp4') ? (somenteAudio ? '.m4a' : '.mp4') : '.webm'
        const r2 = await api.gravar(blob, `tomada ${carimbo}${ext}`, mime)
        if (r2?.aviso) toast('warn', 'Gravação salva com ressalva', r2.aviso)
        else toast('ok', 'Tomada guardada', `${r2.nome} · ${tempo(r2.duracao)}`)
        await listar()
      } catch (e: any) {
        toast('error', 'Não consegui guardar a tomada', String(e?.message ?? e))
      } finally {
        setSalvando(false)
      }
    }
    rec.current = r
    // um pedaço por segundo: se algo derrubar a aba no meio, o que já passou
    // está na mão em vez de se perder inteiro
    r.start(1000)
    setSegundos(0)
    setGravando(true)
    if (texto.trim()) setRolando(true)
  }

  const comecar = () => {
    setErro('')
    // A CONTAGEM existe para ele tirar a mão do mouse e olhar para a lente.
    // Gravar no instante do clique põe o clique no vídeo.
    setContagem(3)
    const id = window.setInterval(() => {
      setContagem((c) => {
        if (c <= 1) {
          window.clearInterval(id)
          comecarDeVerdade()
          return 0
        }
        return c - 1
      })
    }, 1000)
  }

  const parar = () => {
    try { rec.current?.stop() } catch { /* já parado */ }
    rec.current = null
    setGravando(false)
    setRolando(false)
  }

  const apagar = async (t: Tomada) => {
    try {
      await api.apagarGravacao(t.nome)
      setEscolhidas((e) => e.filter((p) => p !== t.path))
      await listar()
      toast('ok', 'Tomada apagada', t.nome)
    } catch (e: any) {
      toast('error', 'Não deu para apagar', String(e?.message ?? e))
    }
  }

  const camaras = dispositivos.filter((d) => d.kind === 'videoinput')
  const micros = dispositivos.filter((d) => d.kind === 'audioinput')

  return (
    <div className="fixed inset-0 z-50 bg-ink-950/95 flex flex-col">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-line">
        <strong className="text-sm">Gravar</strong>
        <span className="text-[11px] text-slate-500">
          a tomada vai para o disco desta máquina — nada sai daqui
        </span>
        {gravando && (
          <span className="chip border-red-700 text-red-300 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            gravando {tempo(segundos)}
          </span>
        )}
        {salvando && <span className="chip">guardando…</span>}
        <button className="btn btn-xs ml-auto" onClick={() => { parar(); onFechar() }}>
          fechar
        </button>
      </div>

      <div className="flex-1 min-h-0 flex">
        {/* ---------------------------------------------------- a imagem */}
        <div className="flex-1 min-w-0 relative bg-black flex items-center justify-center">
          {somenteAudio ? (
            <div className="text-slate-500 text-sm text-center px-6">
              gravando só o áudio — a câmera fica desligada
              {gravando && <div className="mt-3 text-red-300">{tempo(segundos)}</div>}
            </div>
          ) : (
            <video ref={video} autoPlay muted playsInline
                   className="max-w-full max-h-full"
                   // espelhado para ele se ver como num espelho, que é o que
                   // a mão espera ao se ajeitar. O ARQUIVO não sai espelhado:
                   // isto é só o CSS da prévia.
                   style={{ transform: 'scaleX(-1)' }} />
          )}

          {contagem > 0 && (
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-8xl font-bold text-white/90 drop-shadow-lg">
                {contagem}
              </span>
            </div>
          )}

          {/* ----------------------------------------------- teleprompter */}
          {mostrarTexto && texto.trim() && (
            <div ref={prompter}
                 className="absolute left-0 right-0 bottom-0 top-1/4 overflow-hidden
                            px-[8%] py-6 bg-gradient-to-t from-black/85 to-black/50
                            pointer-events-none"
                 style={{ transform: espelhado ? 'scaleX(-1)' : undefined }}>
              <div style={{ fontSize: corpo, lineHeight: 1.45 }}
                   className="text-white font-medium whitespace-pre-wrap text-center">
                {/* respiro no fim para a última linha poder subir até o meio
                    da tela, em vez de parar colada embaixo */}
                {texto}
                <div style={{ height: '50vh' }} />
              </div>
            </div>
          )}
        </div>

        {/* ---------------------------------------------------- controles */}
        <div className="w-[320px] shrink-0 border-l border-line overflow-y-auto
                        p-3 space-y-4 text-xs">
          {erro && (
            <div className="chip border-red-800 text-red-300 block whitespace-normal">
              {erro}
              <div className="text-slate-400 mt-1">
                Se o navegador pediu permissão para a câmera e você negou,
                libere no cadeado ao lado do endereço e clique em tentar de novo.
              </div>
            </div>
          )}

          <div className="space-y-2">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={somenteAudio}
                     onChange={(e) => setSomenteAudio(e.target.checked)} />
              gravar só o áudio
            </label>
            {!somenteAudio && (
              <label className="block">
                <span className="text-slate-500">câmera</span>
                <select className="w-full mt-1" value={camera}
                        onChange={(e) => setCamera(e.target.value)}>
                  <option value="">a padrão</option>
                  {camaras.map((d) => (
                    <option key={d.deviceId} value={d.deviceId}>
                      {d.label || 'câmera'}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="block">
              <span className="text-slate-500">microfone</span>
              <select className="w-full mt-1" value={microfone}
                      onChange={(e) => setMicrofone(e.target.value)}>
                <option value="">o padrão</option>
                {micros.map((d) => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label || 'microfone'}
                  </option>
                ))}
              </select>
            </label>
            <button className="btn btn-xs w-full" onClick={abrirCamera}>
              tentar de novo / trocar aparelho
            </button>
          </div>

          <div className="flex gap-2">
            {!gravando ? (
              <button className="btn btn-primary flex-1"
                      disabled={contagem > 0 || salvando}
                      onClick={comecar}>
                {contagem > 0 ? `${contagem}…` : 'gravar'}
              </button>
            ) : (
              <button className="btn flex-1 border-red-700 text-red-300"
                      onClick={parar}>
                parar
              </button>
            )}
          </div>

          {/* ------------------------------------------- o teleprompter */}
          <div className="space-y-2 border-t border-line pt-3">
            <div className="flex items-center gap-2">
              <strong className="text-slate-300">teleprompter</strong>
              <button className="btn btn-xs ml-auto"
                      onClick={() => setMostrarTexto((v) => !v)}>
                {mostrarTexto ? 'esconder' : 'mostrar'}
              </button>
            </div>
            <textarea className="w-full h-24" value={texto}
                      placeholder="cole aqui o que você vai falar"
                      onChange={(e) => setTexto(e.target.value)} />
            <div className="flex gap-2">
              <button className="btn btn-xs flex-1"
                      onClick={() => setRolando((v) => !v)}
                      disabled={!texto.trim()}>
                {rolando ? 'pausar' : 'rolar'}
              </button>
              <button className="btn btn-xs"
                      onClick={() => {
                        setRolando(false)
                        if (prompter.current) prompter.current.scrollTop = 0
                      }}>
                voltar ao começo
              </button>
            </div>
            <label className="block">
              <span className="text-slate-500">velocidade: {velocidade} px/s</span>
              <input type="range" min={10} max={160} value={velocidade}
                     className="w-full"
                     onChange={(e) => setVelocidade(Number(e.target.value))} />
            </label>
            <label className="block">
              <span className="text-slate-500">tamanho da letra: {corpo}px</span>
              <input type="range" min={16} max={72} value={corpo} className="w-full"
                     onChange={(e) => setCorpo(Number(e.target.value))} />
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={espelhado}
                     onChange={(e) => setEspelhado(e.target.checked)} />
              espelhar o texto
              <span className="text-slate-600">(para vidro de teleprompter)</span>
            </label>
            <p className="text-slate-600 leading-snug">
              O texto começa a rolar sozinho quando você aperta gravar, e para
              ao acabar. Ele não entra no vídeo — só aparece aqui na tela.
            </p>
          </div>
        </div>
      </div>

      {/* -------------------------------------------------- as tomadas */}
      <div className="border-t border-line p-3 max-h-[34vh] overflow-y-auto">
        <div className="flex items-center gap-2 mb-2">
          <strong className="text-xs">tomadas gravadas ({tomadas.length})</strong>
          <span className="text-[11px] text-slate-500">
            marque as que prestaram — elas entram na esteira na ordem em que
            você marcar
          </span>
          <button className="btn btn-xs btn-primary ml-auto"
                  disabled={!escolhidas.length}
                  onClick={() => { parar(); onUsar(escolhidas) }}>
            usar {escolhidas.length || ''} na edição
          </button>
        </div>
        {!tomadas.length && (
          <p className="text-[11px] text-slate-600">
            nenhuma tomada ainda. Aperte gravar aí em cima.
          </p>
        )}
        <div className="space-y-1">
          {tomadas.map((t) => {
            const posicao = escolhidas.indexOf(t.path)
            return (
              <div key={t.path}
                   className="flex items-center gap-2 px-2 py-1 rounded
                              border border-line hover:border-slate-600">
                <button className={`chip w-7 justify-center ${posicao >= 0
                  ? 'border-accent text-accent' : ''}`}
                        title="entra na edição (o número é a ordem da montagem)"
                        onClick={() => setEscolhidas((e) => posicao >= 0
                          ? e.filter((p) => p !== t.path)
                          : [...e, t.path])}>
                  {posicao >= 0 ? posicao + 1 : '+'}
                </button>
                <span className="flex-1 min-w-0 truncate">{t.nome}</span>
                <span className="text-slate-500 font-mono">{tempo(t.duracao)}</span>
                <span className="text-slate-600">{tamanho(t.size_bytes)}</span>
                {!t.tem_audio && (
                  <span className="chip border-amber-800 text-amber-300"
                        title="sem áudio não há fala para transcrever nem silêncio para cortar">
                    sem áudio
                  </span>
                )}
                <button className="btn btn-xs border-red-900 text-red-300"
                        title="apagar esta tomada do disco"
                        onClick={() => apagar(t)}>
                  apagar
                </button>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
