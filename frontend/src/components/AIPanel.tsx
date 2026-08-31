import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { getState, pushHistory, setState, toast, useStore } from '../state/store'
import { SECTIONS } from '../types'

/** A IA opina, o código executa.
 *
 *  Esta tela existe para o usuário VER a sugestão antes de ela virar edição, e
 *  para deixar escrito o que sai da máquina. A regra 1 do projeto continua de
 *  pé — o vídeo não sai — mas o texto sai, e no plano gratuito do Gemini a
 *  Google usa entrada e saída para treinar, com revisor humano podendo ler.
 *  Isso não pode estar escondido num README: fica aqui, na tela, antes do
 *  botão. */
export default function AIPanel({ onChanged }: { onChanged: () => void }) {
  const project = useStore((s) => s.project)
  const activeJob = useStore((s) => s.activeJob)
  const [cfg, setCfg] = useState<any>(null)
  const [chave, setChave] = useState('')
  const [modelo, setModelo] = useState('')
  const [modelos, setModelos] = useState<any[]>([])
  const [testando, setTestando] = useState(false)
  const [teste, setTeste] = useState<any>(null)
  const [plano, setPlano] = useState<any>(null)
  const [comAnexos, setComAnexos] = useState(true)
  const [relatorio, setRelatorio] = useState<any>(null)
  const [comparando, setComparando] = useState(false)
  const [comparacao, setComparacao] = useState<any>(null)

  useEffect(() => {
    api.aiConfig().then((c) => {
      setCfg(c)
      setModelo(c.modelo || '')
      if (c.tem_chave) {
        api.aiModelos().then((r) => setModelos(r.modelos ?? [])).catch(() => {})
      }
    })
  }, [])

  // o job da IA termina e a sugestão aparece — sem aplicar nada
  useEffect(() => {
    if (activeJob?.kind === 'ia' && activeJob.status === 'ok') {
      setPlano(activeJob.result?.plano ?? null)
      setState({ activeJob: null })
      toast('ok', 'A IA leu o roteiro',
        activeJob.result?.leitura || 'Veja a sugestão abaixo antes de aplicar.')
    }
    if (activeJob?.kind === 'ia' && activeJob.status === 'erro') {
      setState({ activeJob: null })
      toast('error', 'A IA não respondeu', activeJob.error)
    }
  }, [activeJob?.id, activeJob?.status])

  const salvar = async (extra: any) => {
    const c = await api.setAiConfig(extra)
    setCfg(c)
    if (extra.chave !== undefined) setChave('')
    return c
  }

  if (!cfg) return <div className="p-4 text-sm text-slate-500">carregando…</div>

  const blocos = plano?.blocos ?? []
  const anexos = plano?.anexos ?? []

  return (
    <div className="p-4 space-y-4 overflow-y-auto">
      <section className="card">
        <h3 className="card-title">Antes de ligar, o que sai da sua máquina</h3>
        <ul className="text-[13px] text-slate-300 space-y-1 list-disc pl-5">
          <li><b>O vídeo não sai.</b> Nem o arquivo, nem o caminho dele, nem um
            segundo de imagem sua. Isso não muda.</li>
          <li><b>O texto sai.</b> A transcrição do que você falou, em blocos, é
            o que a IA lê para entender o roteiro.</li>
          <li><b>Os quadros dos seus anexos saem</b>, em 360 px, e só quando
            você pede ajuda com anexos — sem ver a imagem não dá para decidir
            onde ela cabe.</li>
          <li className="text-amber-300"><b>No plano gratuito do Gemini, a
            Google usa o que entra e o que sai para treinar, e revisores
            humanos podem ler.</b> Só no plano pago (faturamento ligado na conta
            Google) ela garante que não usa. A escolha é sua — mas tem que
            estar escrita.</li>
          <li>A IA só roda quando você aperta o botão, e nunca aplica nada
            sozinha: você vê a sugestão e decide. Sem chave, o editor funciona
            exatamente igual.</li>
        </ul>
      </section>

      <section className="card space-y-3">
        <h3 className="card-title">Chave do Gemini</h3>
        <p className="text-[12px] text-slate-400">
          Cola <b>uma vez</b> e ela fica guardada — no banco local do editor,
          fora da pasta do programa, então sobrevive a atualização e
          reinstalação. Nunca sai por nenhuma rota da API.
        </p>
        {cfg.tem_chave ? (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-emerald-400">chave guardada</span>
            <span className="font-mono text-slate-500">…{cfg.final}</span>
            <button className="btn btn-xs ml-auto"
                    onClick={async () => { await salvar({ chave: '' }); setTeste(null) }}>
              apagar
            </button>
          </div>
        ) : (
          <div className="flex gap-2">
            <input className="input flex-1 font-mono" type="password"
                   placeholder="cole aqui a chave do Google AI Studio"
                   value={chave} onChange={(e) => setChave(e.target.value)} />
            <button className="btn" disabled={!chave.trim()}
                    onClick={async () => {
                      // guardar a chave já traz a lista e o modelo fixado
                      const c = await salvar({ chave: chave.trim() })
                      setModelo(c.modelo || '')
                      api.aiModelos().then((r) => setModelos(r.modelos ?? []))
                        .catch(() => {})
                    }}>guardar</button>
          </div>
        )}
        {/* LISTA, não campo livre. Campo livre com "em branco = o app escolhe"
            é justamente o estado que não pode existir: o modelo tem que ficar
            decidido e escrito antes de o vídeo rodar, senão o programa resolve
            sozinho e o vídeo sai de um modelo que ninguém escolheu. */}
        <div className="flex gap-2 items-center">
          <select className="field flex-1 font-mono text-xs py-1.5"
                  value={modelo} disabled={!cfg.tem_chave}
                  onChange={async (e) => {
                    const id = e.target.value
                    setModelo(id)
                    try { await salvar({ modelo: id }) }
                    catch (err: any) {
                      toast('error', 'Esse modelo não deu',
                        String(err.message ?? err))
                      const c = await api.aiConfig()
                      setCfg(c); setModelo(c.modelo || '')
                    }
                  }}>
            {!modelos.length && <option value={modelo}>{modelo || '…'}</option>}
            {modelos.map((m: any) => (
              <option key={m.id} value={m.id}>{m.id}</option>
            ))}
          </select>
          <button className="btn btn-xs" disabled={!cfg.tem_chave || testando}
                  onClick={async () => {
                    setTestando(true)
                    try {
                      const r = await api.testAi()
                      setTeste(r)
                      toast('ok', 'A chave funciona', `vai usar ${r.modelo}`)
                    } catch (e: any) {
                      setTeste(null)
                      toast('error', 'A chave não passou', e.message)
                    } finally { setTestando(false) }
                  }}>
            {testando ? 'testando…' : 'testar'}
          </button>
        </div>
        {teste && (
          <p className="text-xs text-slate-400">
            vai usar <b className="text-slate-200">{teste.modelo}</b>
            {teste.trocado_de && ` (o modelo "${teste.trocado_de}" não existe mais nessa conta)`}
            {' · '}{teste.disponiveis?.length} modelo(s) disponíveis
          </p>
        )}
      </section>

      <section className="card space-y-3">
        <h3 className="card-title">A IA decide os cortes</h3>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5" checked={cfg.cortes !== false}
                 onChange={(e) => salvar({ cortes: e.target.checked })} />
          <span>
            <b>Automático no EDITAR.</b> Assim que a transcrição sai, a IA lê a
            fala inteira — com as palmas e assobios marcados no lugar — e decide
            quais trechos saem: tentativa refeita, falso começo, contagem,
            muleta. Cada decisão vira um item em <b>"Saiu sozinho"</b>, com o
            motivo e o botão de voltar.
          </span>
        </label>
        <p className="text-[11px] text-slate-500">
          A borda do corte continua sendo do programa: a IA diz <i>o que</i> sai
          (por palavra), o encaixe no vale de energia diz <i>onde</i> exatamente.
          Sem internet ou sem chave, a regra do programa decide sozinha — o
          editor nunca trava por causa da IA.
        </p>
      </section>

      {/* Escolher modelo por opinião é chute e por preço é bobagem: a
          diferença é de centavos por vídeo. Roda os dois no vídeo DELE. */}
      <section className="card space-y-3">
        <h3 className="card-title">Qual modelo corta melhor o SEU vídeo</h3>
        <p className="text-[13px] text-slate-400">
          Roda os dois no mesmo vídeo e mostra o que cada um quis cortar, com o
          motivo. O teste inteiro custa menos de dez centavos — decida olhando,
          não no escuro.
        </p>
        <button className="btn" disabled={!cfg.tem_chave || comparando || !project}
                onClick={async () => {
                  setComparando(true)
                  try {
                    const r = await api.compararModelos(project!.id,
                      ['gemini-3.7-flash', 'gemini-3.1-pro'])
                    setComparacao(r)
                  } catch (e: any) {
                    toast('error', 'Não deu para comparar', e.message)
                  } finally { setComparando(false) }
                }}>
          {comparando ? 'rodando os dois…' : 'Comparar modelos neste vídeo'}
        </button>
        {comparacao && (
          <div className="grid gap-3 sm:grid-cols-2">
            {comparacao.resultados.map((r: any) => (
              <div key={r.modelo} className="card p-2.5 space-y-1.5">
                <div className="flex items-baseline gap-2">
                  <b className="text-[13px] text-slate-200">{r.modelo}</b>
                  {r.ok && (
                    <span className="text-[11px] text-slate-500">
                      {r.cortes.length} corte(s) · {r.segundos_fora}s fora
                    </span>
                  )}
                </div>
                {!r.ok && <p className="text-[12px] text-red-300">{r.erro}</p>}
                {r.ok && r.cortes.map((c: any, i: number) => (
                  <div key={i} className="text-[11px] border-t border-line pt-1">
                    <span className={`chip mr-1 ${c.tipo === 'copy'
                      ? 'border-sky-800 text-sky-300'
                      : 'border-amber-800 text-amber-300'}`}>{c.tipo}</span>
                    <span className="text-slate-500 line-through">{c.texto}</span>
                    <div className="text-slate-400 mt-0.5">{c.motivo}</div>
                  </div>
                ))}
                {r.ok && (
                  <button className="btn btn-xs w-full mt-1"
                          onClick={async () => {
                            await salvar({ modelo: r.modelo })
                            toast('ok', `Agora usando ${r.modelo}`,
                              'Aperte refazer edição para aplicar.')
                          }}>
                    usar este
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="card space-y-3">
        <h3 className="card-title">Ler o roteiro</h3>
        <p className="text-[13px] text-slate-400">
          A IA diz em que etapa cada bloco está e onde o ritmo pede um plano mais
          fechado. Ela não escolhe tempo de corte, nem valor de zoom, nem posição:
          quem faz isso é o programa, com as mesmas regras de sempre.
        </p>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={comAnexos}
                 onChange={(e) => setComAnexos(e.target.checked)} />
          incluir os anexos (manda um quadro de cada mídia sua)
        </label>
        <button className="btn btn-primary" disabled={!cfg.tem_chave || !project}
                onClick={async () => {
                  try {
                    const job = await api.aiPlan(project!.id, comAnexos)
                    setState({ activeJob: job })
                  } catch (e: any) { toast('error', 'Não deu', e.message) }
                }}>
          Ler o roteiro
        </button>
      </section>

      {plano && (
        <section className="card space-y-3">
          <h3 className="card-title">O que a IA propôs</h3>
          {plano.leitura && (
            <p className="text-sm text-slate-300 italic">"{plano.leitura}"</p>
          )}
          <div className="max-h-64 overflow-y-auto text-[12px] space-y-1">
            {blocos.map((b: any) => (
              <div key={b.i} className="flex items-center gap-2">
                <span className="font-mono text-slate-600 w-8 text-right">{b.i}</span>
                <span className="px-1.5 rounded text-ink-900 font-medium"
                      style={{ background: SECTIONS[b.etapa]?.color ?? '#64748b' }}>
                  {SECTIONS[b.etapa]?.label ?? b.etapa}
                </span>
                {b.enfase !== 'normal' && (
                  <span className="text-slate-400">
                    {b.enfase === 'fechado' ? 'fecha' : 'abre'}
                  </span>
                )}
                <span className="text-slate-500 truncate">{b.porque}</span>
              </div>
            ))}
          </div>
          {anexos.length > 0 && (
            <div className="text-[12px] space-y-1 border-t border-line pt-2">
              {anexos.map((a: any, i: number) => (
                <div key={i} className="text-slate-400">
                  anexo <b className="text-slate-200">{a.midia}</b> no bloco{' '}
                  <b className="text-slate-200">{a.bloco}</b> por {a.segundos}s
                  {' — '}{a.porque}
                </div>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <button className="btn btn-primary"
                    onClick={async () => {
                      const s = getState()
                      if (project?.plan) {
                        pushHistory(project.plan, s.removedWordIds,
                          project.analysis?.manual_removed_word_ids ?? [])
                      }
                      try {
                        const r = await api.applyAiPlan(project!.id, plano)
                        setRelatorio(r)
                        setPlano(null)
                        await onChanged()
                        toast('ok', `${r.aplicados?.length ?? 0} bloco(s) ajustado(s)`,
                          r.recusados?.length
                            ? `${r.recusados.length} sugestão(ões) não couberam — veja abaixo`
                            : 'tudo coube')
                      } catch (e: any) { toast('error', 'Não deu', e.message) }
                    }}>
              Aplicar
            </button>
            <button className="btn" onClick={() => setPlano(null)}>Descartar</button>
          </div>
        </section>
      )}

      {relatorio?.recusados?.length > 0 && (
        <section className="card">
          <h3 className="card-title">O que eu não deixei passar</h3>
          <ul className="text-[12px] text-slate-400 space-y-1 list-disc pl-5">
            {relatorio.recusados.map((r: any, i: number) => (
              <li key={i}><b className="text-slate-300">{r.o_que}</b>: {r.motivo}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
