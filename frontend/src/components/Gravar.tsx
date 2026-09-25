import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { toast } from '../state/store'
import { desvioDoOlhar, ehCameraVirtual, linhaParaOAngulo } from '../lib/olhar'

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
  // ONDE O OLHO OLHA. A linha de leitura é onde o texto passa; a lente é onde
  // a câmera está. A diferença entre as duas é um ÂNGULO, e é o ângulo que
  // quem assiste vê — não o texto. Por isso os dois são ajustáveis e o desvio
  // é mostrado em graus: adivinhar "está mais ou menos na altura da câmera"
  // é o que faz o criativo sair com cara de quem está lendo.
  const [linhaY, setLinhaY] = useState(0.14)
  const [lenteY, setLenteY] = useState(0)
  const [diagonal, setDiagonal] = useState(24)
  const [distancia, setDistancia] = useState(60)
  const [alturaJanela, setAlturaJanela] = useState(
    typeof window === 'undefined' ? 1080 : window.innerHeight)
  const [proporcaoTela, setProporcaoTela] = useState(
    typeof window === 'undefined' ? 16 / 9
      : window.screen.width / Math.max(1, window.screen.height))

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

  // A conta do ângulo depende da altura da JANELA em pixels: sem ouvir o
  // resize, mudar a janela deixava o número certo para o tamanho de antes.
  useEffect(() => {
    const medir = () => setAlturaJanela(window.innerHeight)
    window.addEventListener('resize', medir)
    return () => window.removeEventListener('resize', medir)
  }, [])

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

  const olhar = desvioDoOlhar({
    lenteY, linhaY, alturaJanelaPx: alturaJanela,
    diagonalPolegadas: diagonal, proporcaoTela, distanciaCm: distancia,
  })
  const corDoOlhar = olhar.veredito === 'imperceptivel' ? 'text-emerald-300'
    : olhar.veredito === 'leve' ? 'text-amber-300' : 'text-red-300'
  const camaras = dispositivos.filter((d) => d.kind === 'videoinput')
  const virtual = camaras.find((d) => ehCameraVirtual(d.label))
  const usandoVirtual = !!virtual && (camera === virtual.deviceId
    || (!camera && camaras[0]?.deviceId === virtual.deviceId))
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

          {/* ------------------------------------------------- a lente
              A marca de onde a câmera está. Sem ela, "olhe para a câmera" é um
              conselho sem endereço: em notebook a lente fica acima da borda de
              cima, em monitor com webcam presa fica um pouco abaixo, e em
              webcam de mesa pode ficar em qualquer altura. */}
          <div className="absolute left-1/2 -translate-x-1/2 pointer-events-none
                          flex flex-col items-center"
               style={{ top: `max(2px, ${lenteY * 100}%)` }}>
            <div className="w-3 h-3 rounded-full border-2 border-accent
                            bg-accent/30" />
            <span className="text-[10px] text-accent/90 mt-0.5">lente</span>
          </div>

          {/* ----------------------------------------------- teleprompter
              COLUNA ESTREITA NA ALTURA DA LENTE, não um bloco embaixo.
              O bloco de antes cobria os três quartos de baixo da tela: ler ali
              é olhar PARA BAIXO, que é o desvio mais visível que existe. Numa
              tela de 24" a 60 cm, texto no meio da tela dá 14° de desvio —
              aparece. Colado na lente dá 1,4° — ninguém nota. A coluna é
              estreita pelo mesmo motivo no eixo horizontal: linha curta mantém
              o olho perto do centro em vez de varrer a tela de ponta a ponta.
              A conta está em lib/olhar.ts e o número aparece ao lado. */}
          {mostrarTexto && texto.trim() && (
            <>
              <div ref={prompter}
                   className="absolute overflow-hidden pointer-events-none"
                   style={{
                     top: `${linhaY * 100}%`,
                     left: '50%',
                     width: 'min(46%, 620px)',
                     height: '30%',
                     transform: `translate(-50%, -50%)${espelhado ? ' scaleX(-1)' : ''}`,
                     // o texto entra e sai desbotando: borda dura corta a
                     // palavra no meio e o olho volta para procurá-la
                     maskImage: 'linear-gradient(to bottom, transparent, #000 22%,'
                       + ' #000 78%, transparent)',
                     WebkitMaskImage: 'linear-gradient(to bottom, transparent,'
                       + ' #000 22%, #000 78%, transparent)',
                   }}>
                <div style={{ fontSize: corpo, lineHeight: 1.5,
                              paddingTop: '15%', paddingBottom: '15%',
                              textShadow: '0 2px 10px rgba(0,0,0,.95)' }}
                     className="text-white font-semibold whitespace-pre-wrap
                                text-center">
                  {texto}
                  {/* respiro no fim: a última linha precisa poder subir até a
                      linha de leitura, em vez de parar antes dela */}
                  <div style={{ height: '30vh' }} />
                </div>
              </div>
              {/* a linha de leitura, para ele saber onde o olho tem que ficar */}
              <div className="absolute left-0 right-0 pointer-events-none
                              border-t border-accent/25"
                   style={{ top: `${linhaY * 100}%` }} />
            </>
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
                <select className="field w-full mt-1" value={camera}
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
              <select className="field w-full mt-1" value={microfone}
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
            {/* A classe `field` é o que dá fundo escuro ao campo. Sem ela o
                navegador pinta o fundo de BRANCO e o texto herda a cor clara
                do app: branco no branco, e ele colava o roteiro sem ver o que
                tinha colado. */}
            <textarea className="field w-full h-24 font-sans" value={texto}
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

          {/* ------------------------------------------- o olhar na lente
              Esta é a parte que decide se o criativo tem cara de quem está
              lendo. O desvio do olhar é um ÂNGULO: a diferença entre onde o
              texto está e onde a lente está. Abaixo de 4° ninguém nota; acima
              de 8° aparece. Em vez de "deixe mais ou menos na altura da
              câmera", aqui está o número. */}
          <div className="space-y-2 border-t border-line pt-3">
            <div className="flex items-center gap-2">
              <strong className="text-slate-300">olhar na lente</strong>
              <span className={`chip ml-auto ${corDoOlhar}`}>
                {olhar.graus}° de desvio
              </span>
            </div>
            <p className={`leading-snug ${corDoOlhar}`}>{olhar.recado}</p>
            <button className="btn btn-xs w-full btn-primary"
                    onClick={() => setLinhaY(linhaParaOAngulo({
                      lenteY, alturaJanelaPx: alturaJanela,
                      diagonalPolegadas: diagonal, proporcaoTela,
                      distanciaCm: distancia,
                    }))}>
              colar o texto na lente
            </button>
            <label className="block">
              <span className="text-slate-500">
                altura do texto: {Math.round(linhaY * 100)}% da tela
              </span>
              <input type="range" min={2} max={90} value={Math.round(linhaY * 100)}
                     className="w-full"
                     onChange={(e) => setLinhaY(Number(e.target.value) / 100)} />
            </label>
            <label className="block">
              <span className="text-slate-500">
                altura da lente: {Math.round(lenteY * 100)}%
                {lenteY === 0 && ' (notebook / webcam em cima)'}
              </span>
              <input type="range" min={0} max={100} value={Math.round(lenteY * 100)}
                     className="w-full"
                     onChange={(e) => setLenteY(Number(e.target.value) / 100)} />
            </label>
            <div className="flex gap-2">
              <label className="flex-1">
                <span className="text-slate-500">tela (polegadas)</span>
                <input type="number" min={10} max={80} value={diagonal}
                       className="field w-full mt-1"
                       onChange={(e) => setDiagonal(Number(e.target.value) || 24)} />
              </label>
              <label className="flex-1">
                <span className="text-slate-500">distância (cm)</span>
                <input type="number" min={20} max={300} value={distancia}
                       className="field w-full mt-1"
                       onChange={(e) => setDistancia(Number(e.target.value) || 60)} />
              </label>
            </div>
            <p className="text-slate-600 leading-snug">
              Esses dois números existem porque o mesmo texto na mesma altura
              desvia mais numa tela grande e menos numa pequena — o que conta é
              o ângulo, não o tanto de pixels.
            </p>

            {/* A CORREÇÃO NEURAL DO OLHAR, quando ela existe na máquina.
                NVIDIA Broadcast e companhia redesenham o olho e expõem uma
                CÂMERA VIRTUAL. Como esta tela lista todas as câmeras, ela já
                funciona aqui — só escolher. Quem tem a placa quase sempre não
                sabe que o recurso está ali, então vale dizer. */}
            {virtual && !usandoVirtual && (
              <div className="chip border-accent/60 text-accent block
                              whitespace-normal leading-snug">
                Achei <b>{virtual.label}</b> nas suas câmeras. Se ela tiver
                correção de olhar ligada, escolha ela na lista de câmeras aí em
                cima: a imagem chega aqui já com o olho apontado para a lente.
              </div>
            )}
            {usandoVirtual && (
              <div className="chip border-emerald-700 text-emerald-300 block
                              whitespace-normal leading-snug">
                Gravando pela <b>{virtual?.label}</b>. Se a correção de olhar
                estiver ligada nela, o desvio acima deixa de importar.
              </div>
            )}
            {!virtual && (
              <p className="text-slate-600 leading-snug">
                Existe tecnologia que <b>redesenha o olho</b> para ele apontar
                para a lente — o NVIDIA Broadcast faz isso de graça em placa
                RTX. Ele cria uma câmera virtual; instalado, ela aparece na
                lista de câmeras aí em cima e a imagem já chega corrigida, sem
                o editor precisar de nada.
              </p>
            )}
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
