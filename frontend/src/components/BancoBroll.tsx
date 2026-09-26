import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { getPlayhead, toast, useStore } from '../state/store'

/**
 * BANCO DE B-ROLL GRÁTIS: Pexels e Pixabay, uso livre inclusive comercial.
 *
 * Só a palavra da busca sai do computador; o vídeo dele não vai a lugar
 * nenhum. O escolhido é BAIXADO para a biblioteca local e entra como b-roll
 * no cursor, com a fala por baixo. A tela nunca diz de onde baixar: ela manda
 * o ID, e o servidor acha o arquivo na resposta do próprio banco.
 */
const ORIENTACOES: Record<string, string> = {
  '': 'do vídeo', portrait: 'em pé', landscape: 'deitado', square: 'quadrado',
}

const GRADE = { gridTemplateColumns: 'repeat(auto-fill, minmax(104px, 1fr))' }

export default function BancoBroll({ projectId, onChanged, snapshot }: {
  projectId: string; onChanged: () => Promise<any>; snapshot: () => void
}) {
  const [estado, setEstado] = useState<any>(null)
  const [editandoChaves, setEditandoChaves] = useState(false)
  const [chaves, setChaves] = useState({ pexels: '', pixabay: '' })
  const [termo, setTermo] = useState('')
  const [sugestao, setSugestao] = useState<any>(null)
  const [orientacao, setOrientacao] = useState('')
  const [res, setRes] = useState<any>(null)
  const [pagina, setPagina] = useState(1)
  const [buscando, setBuscando] = useState(false)
  const [marcados, setMarcados] = useState<string[]>([])
  const [passando, setPassando] = useState<string | null>(null)
  const [baixados, setBaixados] = useState<any[]>([])
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useStore((s) => (jobId ? s.jobs[jobId] : undefined))
  const [palavrasNovas, setPalavrasNovas] = useState('')
  const [editandoBib, setEditandoBib] = useState(false)

  const enviarMeus = async () => {
    try {
      const r = await api.escolher('video', 'Escolher vídeos para a biblioteca de b-roll', true)
      if (r.cancelado) return
      const paths = (r.paths?.length ? r.paths : [r.path]).filter(Boolean)
      toast('info', `Copiando ${paths.length} vídeo(s) para a biblioteca…`,
        'Os originais ficam onde estão.')
      const res = await api.bancoEnviar(paths, palavrasNovas)
      carregarBaixados()
      toast('ok', `${res.guardados?.length ?? 0} vídeo(s) na biblioteca`,
        palavrasNovas ? `Palavras-chave: ${palavrasNovas}`
          : 'Dica: ponha palavras-chave para o b-roll automático achar estes vídeos.')
      for (const x of res.recusados ?? []) toast('warn', 'Um não entrou', x.motivo)
    } catch (e: any) {
      toast('warn', 'Não deu para enviar', String(e.message ?? e))
    }
  }

  const carregarEstado = () =>
    api.bancoEstado().then(setEstado).catch(() => setEstado(null))
  const carregarBaixados = () =>
    api.bancoBaixados().then(setBaixados).catch(() => setBaixados([]))
  const sugerir = async (preencher: boolean) => {
    try {
      const s = await api.bancoSugestao(projectId, getPlayhead())
      setSugestao(s)
      if (preencher && s.termos?.length) setTermo((t) => t || s.termos[0])
    } catch { /* sem sugestão a busca continua valendo */ }
  }

  useEffect(() => {
    carregarEstado(); carregarBaixados(); sugerir(true)
  }, [projectId])   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!job || !jobId) return
    if (job.status === 'ok') {
      setJobId(null)
      setMarcados([])
      carregarBaixados()
      onChanged().then(() => {
        const r = job.result ?? {}
        const postos = r.postos ?? []
        const autores = Array.from(new Set((r.creditos ?? [])
          .map((c: any) => c.autor).filter(Boolean))).join(', ')
        if (postos.length) {
          toast('ok', postos.length === 1 ? 'B-roll do banco no vídeo'
            : `${postos.length} b-rolls do banco, um depois do outro`,
            `A partir de ${timecode(postos[0].out_start)}. A fala continua por baixo.`
            + (autores ? ` Vídeos de ${autores}.` : ''))
        }
        for (const x of r.recusados ?? []) {
          toast('warn', 'Um não entrou', `${String(x.path).split(/[\\/]/).pop()}: ${x.motivo}`)
        }
      })
    } else if (job.status === 'erro' || job.status === 'cancelado') {
      setJobId(null)
      toast('error', 'O b-roll do banco não entrou', job.error || job.message)
    }
  }, [job?.status])   // eslint-disable-line react-hooks/exhaustive-deps

  const buscar = async (pag = 1) => {
    if (!termo.trim()) { toast('warn', 'Escreva o que procurar'); return }
    setBuscando(true)
    try {
      const r = await api.bancoBuscar(termo.trim(), orientacao, projectId, pag)
      setPagina(pag)
      setRes((antes: any) => (pag > 1 && antes
        ? { ...r, itens: [...antes.itens, ...r.itens] } : r))
      if (pag === 1) setMarcados([])
      for (const a of r.avisos ?? []) toast('warn', 'Um dos bancos não respondeu', a)
      if (!r.itens?.length) toast('info', 'Nada achado', 'Tente uma palavra mais simples.')
    } catch (e: any) {
      toast('warn', 'A busca não deu', String(e.message ?? e))
    } finally { setBuscando(false) }
  }

  const guardarChaves = async () => {
    const corpo: Record<string, string> = {}
    if (chaves.pexels.trim()) corpo.pexels = chaves.pexels.trim()
    if (chaves.pixabay.trim()) corpo.pixabay = chaves.pixabay.trim()
    if (!Object.keys(corpo).length) { toast('warn', 'Cole pelo menos uma chave'); return }
    try {
      const e = await api.bancoChaves(corpo)
      setEstado(e)
      setChaves({ pexels: '', pixabay: '' })
      const ruins = (Object.keys(corpo) as ('pexels' | 'pixabay')[])
        .filter((f) => e?.[f]?.funciona === false)
      if (ruins.length) {
        toast('warn', 'A chave não funcionou', ruins.map((f) => e[f].aviso).join(' · '))
      } else {
        setEditandoChaves(false)
        toast('ok', 'Chave guardada e funcionando', 'Fica neste computador. Já dá para buscar.')
      }
    } catch (e: any) {
      toast('error', 'Não guardei a chave', String(e.message ?? e))
    }
  }

  const usar = async (ids: string[]) => {
    if (!ids.length) return
    snapshot()
    try {
      const j = await api.bancoUsar(projectId, ids, getPlayhead(), termo)
      setJobId(j.id)
    } catch (e: any) {
      toast('error', 'Não deu para baixar', String(e.message ?? e))
    }
  }

  const usarBaixado = async (b: any) => {
    snapshot()
    try {
      const r = await api.brolls(projectId, [b.path], getPlayhead())
      await onChanged()
      if (r.postos?.length) {
        toast('ok', 'B-roll no vídeo', `Da biblioteca, sem internet. Vídeo de ${b.autor}.`)
      }
      for (const x of r.recusados ?? []) toast('warn', 'Não entrou', x.motivo)
    } catch (e: any) {
      toast('warn', 'O b-roll não entrou', String(e.message ?? e))
    }
  }

  const ocupado = !!jobId && !!job && ['fila', 'rodando'].includes(job.status)
  const semChave = estado && !estado.alguma
  const itens: any[] = res?.itens ?? []

  return (
    <section className="card p-3" data-banco-broll="1">
      <div className="flex items-center gap-2 mb-2">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
          Banco de b-roll grátis
        </h3>
        <span className="text-[10px] text-slate-500">
          vídeos do{' '}
          <a className="underline hover:text-slate-300" href="https://www.pexels.com"
             target="_blank" rel="noreferrer">Pexels</a> e do{' '}
          <a className="underline hover:text-slate-300" href="https://pixabay.com"
             target="_blank" rel="noreferrer">Pixabay</a>
          {' '}· uso livre, inclusive em anúncio
        </span>
        {estado?.alguma && (
          <button className="btn btn-xs ml-auto" onClick={() => setEditandoChaves((v) => !v)}>
            chaves</button>
        )}
      </div>

      {(semChave || editandoChaves) && (
        <div className="border border-line rounded-md p-2.5 mb-2 space-y-2" data-banco-chaves="1">
          <p className="text-[11px] text-slate-300 leading-snug">
            Os dois bancos são grátis, mas pedem uma <b>chave</b> (1 minuto, só criar a
            conta). Basta uma das duas; com as duas a busca acha mais. A chave fica
            neste computador. Só a palavra buscada sai daqui — o seu vídeo, nunca.
          </p>
          {(['pexels', 'pixabay'] as const).map((f) => (
            <div key={f} className="flex items-center gap-2">
              <span className="text-xs w-24 text-slate-300">
                {f === 'pexels' ? 'Pexels' : 'Pixabay'}
                {estado?.[f]?.funciona === true && <span className="text-emerald-300"> ✓</span>}
                {estado?.[f]?.funciona === false && <span className="text-red-300" title={estado[f].aviso}> ✗</span>}
              </span>
              <input className="field flex-1 font-mono text-xs" type="password"
                     placeholder={estado?.[f]?.tem_chave
                       ? `guardada (…${estado[f].final}) — cole outra para trocar`
                       : 'cole a chave aqui'}
                     value={chaves[f]}
                     onChange={(e) => setChaves((c) => ({ ...c, [f]: e.target.value }))}
                     onKeyDown={(e) => { if (e.key === 'Enter') guardarChaves() }} />
              <a className="btn btn-xs shrink-0" target="_blank" rel="noreferrer"
                 href={estado?.[f]?.onde_criar
                   ?? (f === 'pexels' ? 'https://www.pexels.com/api/' : 'https://pixabay.com/api/docs/')}>
                criar chave grátis</a>
            </div>
          ))}
          <button className="btn btn-xs btn-primary" onClick={guardarChaves}>guardar</button>
        </div>
      )}

      {!semChave && (
        <>
          <div className="flex flex-wrap gap-2 items-center">
            <input className="field flex-1 min-w-[12rem] text-xs" value={termo}
                   placeholder="o que mostrar? ex.: academia, café, dinheiro"
                   onChange={(e) => setTermo(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') buscar(1) }} />
            <select className="field w-28 text-xs" value={orientacao}
                    title="a orientação dos vídeos; 'do vídeo' segue o formato do seu"
                    onChange={(e) => setOrientacao(e.target.value)}>
              {Object.entries(ORIENTACOES).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
            <button className="btn btn-xs btn-primary" disabled={buscando}
                    onClick={() => buscar(1)}>{buscando ? 'buscando…' : 'buscar'}</button>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 mt-1.5 text-[10px] text-slate-500">
            <button className="underline hover:text-slate-300" onClick={() => sugerir(false)}
                    title="lê o que está sendo dito no ponto do cursor">
              sugerir pela fala em {timecode(getPlayhead())}</button>
            {(sugestao?.termos ?? []).map((t: string) => (
              <button key={t} className="chip border-line text-slate-300 hover:border-sky-500"
                      onClick={() => setTermo(t)}>{t}</button>
            ))}
            {sugestao?.texto && (
              <span className="truncate max-w-[22rem]" title={sugestao.texto}>
                “{sugestao.texto}”</span>
            )}
          </div>

          {itens.length > 0 && (
            <>
              {/* a grade segue a largura do PAINEL, não da janela: o painel
                  mora numa coluna estreita e 6 colunas viravam palitos */}
              <div className="grid gap-1.5 mt-2" style={GRADE}>
                {itens.map((it) => {
                  const marcado = marcados.includes(it.id)
                  return (
                    <button key={it.id} data-item-banco={it.id}
                            className={`relative rounded-md overflow-hidden border text-left
                              ${marcado ? 'border-sky-400 ring-2 ring-sky-400/60' : 'border-line'}`}
                            onMouseEnter={() => setPassando(it.id)}
                            onMouseLeave={() => setPassando(null)}
                            onClick={() => setMarcados((m) => (m.includes(it.id)
                              ? m.filter((x) => x !== it.id) : [...m, it.id]))}
                            title={`${it.descricao || termo} — de ${it.autor} no ${
                              it.fonte === 'pexels' ? 'Pexels' : 'Pixabay'}. Clique para marcar.`}>
                      <div className="bg-ink-900"
                           style={{ aspectRatio: it.largura && it.altura
                             ? `${it.largura} / ${it.altura}` : '9 / 16', maxHeight: 180 }}>
                        {passando === it.id && it.previa ? (
                          <video src={it.previa} autoPlay muted loop playsInline
                                 className="w-full h-full object-cover" />
                        ) : it.miniatura ? (
                          <img src={it.miniatura} alt="" loading="lazy"
                               className="w-full h-full object-cover" />
                        ) : null}
                      </div>
                      <span className="absolute top-1 left-1 chip bg-black/70 border-0 text-[9px]">
                        {Math.round(it.duracao)} s</span>
                      {marcado && (
                        <span className="absolute top-1 right-1 chip bg-sky-500 border-0 text-[9px] text-white">
                          {marcados.indexOf(it.id) + 1}</span>
                      )}
                      <span className="block px-1 py-0.5 text-[9px] text-slate-400 truncate bg-ink-800">
                        {it.autor} · {it.fonte === 'pexels' ? 'Pexels' : 'Pixabay'}</span>
                    </button>
                  )
                })}
              </div>
              <div className="flex flex-wrap items-center gap-2 mt-2">
                <button className="btn btn-xs btn-primary" data-usar-banco="1"
                        disabled={!marcados.length || ocupado}
                        onClick={() => usar(marcados)}>
                  {ocupado ? (job?.message || 'baixando…')
                    : marcados.length
                      ? `pôr ${marcados.length === 1 ? 'o marcado' : `os ${marcados.length}`} em ${timecode(getPlayhead())}`
                      : 'marque um ou mais vídeos'}
                </button>
                {marcados.length > 1 && (
                  <span className="text-[10px] text-slate-500">
                    entram na ordem em que você marcou, um depois do outro</span>
                )}
                <button className="btn btn-xs ml-auto" disabled={buscando}
                        onClick={() => buscar(pagina + 1)}>mais resultados</button>
              </div>
            </>
          )}
        </>
      )}

      {/* A BIBLIOTECA DE B-ROLL: o que veio do banco e o que ele enviou.
          Fica na pasta de dados e serve para todos os vídeos, sem internet.
          As palavras-chave são o que o b-roll automático procura. */}
      <div className="mt-3 border-t border-line pt-2" data-biblioteca="1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold text-slate-300 uppercase tracking-wide">
            Biblioteca de b-roll ({baixados.length})</span>
          <input className="field text-xs py-0.5 w-44" value={palavrasNovas}
                 placeholder="palavras-chave (ex.: academia, treino)"
                 title="o que o b-roll automático vai procurar para usar estes vídeos"
                 onChange={(e) => setPalavrasNovas(e.target.value)} />
          <button className="btn btn-xs" data-enviar-meus="1" onClick={enviarMeus}>
            + enviar vídeos meus</button>
          {baixados.length > 0 && (
            <button className={`btn btn-xs ml-auto ${editandoBib ? 'btn-primary' : ''}`}
                    onClick={() => setEditandoBib((v) => !v)}>
              {editandoBib ? 'pronto' : 'editar'}</button>
          )}
        </div>
        {baixados.length === 0 ? (
          <p className="hint mt-1">
            Vazia. O que você baixar do banco e os vídeos que você enviar ficam aqui,
            para usar em qualquer vídeo, sem internet.</p>
        ) : (
          <div className="grid gap-1.5 mt-1.5 max-h-72 overflow-auto" style={GRADE}>
            {baixados.map((b) => (
              <div key={b.id} className="rounded-md overflow-hidden border border-line text-left">
                <button className="block w-full text-left"
                        title={`${b.termo || b.nome || ''} — de ${b.autor}. Clique para pôr no cursor.`}
                        onClick={() => usarBaixado(b)}>
                  {b.miniatura
                    ? <img src={b.miniatura} alt="" loading="lazy" className="w-full h-20 object-cover"
                           onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                    : <div className="h-20 bg-ink-900" />}
                  <span className="block px-1 py-0.5 text-[9px] text-slate-400 truncate">
                    {b.fonte === 'meu' ? '★ ' : ''}{b.termo || b.nome || b.arquivo} · {b.autor}</span>
                </button>
                {editandoBib && (
                  <div className="p-1 space-y-1 bg-ink-900">
                    <input className="field w-full text-[10px] py-0.5" defaultValue={b.termo || ''}
                           placeholder="palavras-chave"
                           onBlur={async (e) => {
                             if (e.target.value === (b.termo || '')) return
                             await api.bancoPalavras(b.id, e.target.value).catch(() => null)
                             carregarBaixados()
                           }} />
                    <button className="btn btn-xs btn-danger w-full"
                            onClick={async () => {
                              await api.bancoTirar(b.id).catch(() => null)
                              carregarBaixados()
                            }}>tirar da biblioteca</button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
