import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { getPlayhead, setPlayhead, setState, toast, useStore } from '../state/store'
import { EFEITOS_CAMADA, TIPOS_CENA, TIPOS_GRAFICO, TIPOS_TRANSICAO } from './PosInspector'

const NOME_TIPO = Object.fromEntries(TIPOS_GRAFICO)
const NOME_EFEITO = Object.fromEntries(EFEITOS_CAMADA)
const NOME_TRANS = Object.fromEntries(TIPOS_TRANSICAO)
const NOME_CENA = Object.fromEntries(TIPOS_CENA)

// os atalhos: cada um põe um item pronto no cursor, para ajustar depois
const RAPIDOS: { rotulo: string; tipo: 'graficos' | 'camadas' | 'cenas'; dados: any; dura?: number }[] = [
  { rotulo: 'logo de lado', tipo: 'graficos',
    dados: { tipo: 'logo', logo: 'simbolo', x: 0.92, y: 0.12, tamanho: 0.7, opacidade: 0.8 }, dura: 8 },
  { rotulo: 'moldura', tipo: 'cenas', dados: { tipo: 'moldura', fundo: 'desfoque' }, dura: 6 },
  { rotulo: 'camadas de vidro', tipo: 'cenas', dados: { tipo: 'vidro3d', logos: ['assinatura_branca'] }, dura: 5 },
  { rotulo: 'título em vidro', tipo: 'graficos',
    dados: { tipo: 'titulo', texto: 'TÍTULO', y: 0.2, estilo: 'vidro', entrada: 'subir' } },
  { rotulo: 'título', tipo: 'graficos', dados: { tipo: 'titulo', texto: 'TÍTULO', y: 0.2, entrada: '3d' } },
  { rotulo: 'tela de tópico', tipo: 'graficos',
    dados: { tipo: 'tela', texto: 'Tópico', prefixo: 'PARTE 1', entrada: 'pop' } },
  { rotulo: 'lista', tipo: 'graficos',
    dados: { tipo: 'lista', texto: 'Em 3 passos', itens: ['Primeiro', 'Segundo', 'Terceiro'] } },
  { rotulo: 'número', tipo: 'graficos', dados: { tipo: 'numero', numero: 100, sufixo: '%', texto: 'legenda' } },
  { rotulo: 'gráfico subindo (barras)', tipo: 'graficos',
    dados: { tipo: 'barras', texto: 'Resultado', valores: [12, 19, 27, 41],
             rotulos: ['jan', 'fev', 'mar', 'abr'], y: 0.32 }, dura: 4 },
  { rotulo: 'gráfico de linha', tipo: 'graficos',
    dados: { tipo: 'linha', texto: 'Crescimento', valores: [3, 4.1, 3.8, 5.6, 8.9],
             prefixo: 'R$ ', sufixo: ' mil', y: 0.32 }, dura: 4 },
  { rotulo: 'rosca %', tipo: 'graficos', dados: { tipo: 'rosca', numero: 87, texto: 'legenda', y: 0.3 }, dura: 4 },
  { rotulo: 'ícone ✓', tipo: 'graficos', dados: { tipo: 'icone', icone: 'check', texto: '', y: 0.26 }, dura: 3 },
  { rotulo: 'destaque', tipo: 'graficos', dados: { tipo: 'destaque', texto: 'PALAVRA' } },
  { rotulo: 'nome', tipo: 'graficos', dados: { tipo: 'nome', texto: 'Seu nome', subtexto: 'o que você faz' } },
  { rotulo: 'texto atrás da pessoa', tipo: 'graficos',
    dados: { tipo: 'titulo', texto: 'TÍTULO', y: 0.18, tamanho: 1.6, camada: 'atras', estilo: 'limpo' } },
  { rotulo: 'fundo desfocado', tipo: 'camadas', dados: { efeito: 'desfoque' } },
  { rotulo: 'holofote', tipo: 'camadas', dados: { efeito: 'escurecer' } },
  { rotulo: '3D', tipo: 'camadas', dados: { efeito: 'parallax' } },
]

/**
 * A PÓS-EDIÇÃO: o que o Sharkcut entrega por cima do corte — títulos, telas
 * de tópico, listas, números, transições, fundo desfocado, texto atrás da
 * pessoa. O Claude faz isso pelo MCP; aqui você vê o que ele pôs, ajusta,
 * apaga e acrescenta. Tudo sai no MESMO encode do vídeo, sem segunda geração.
 */
export default function PosPanel({ onChanged, snapshot, onSelect }: {
  onChanged: () => Promise<any>; snapshot: () => void
  onSelect: (kind: string, id: string) => void
}) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const [recorte, setRecorte] = useState<any>(null)
  const activeJob = useStore((s) => s.activeJob)
  const claudeRodando = activeJob?.kind === 'claude' && ['fila', 'rodando'].includes(activeJob.status)
  const [baixando, setBaixando] = useState(false)

  useEffect(() => {
    api.recorteEstado().then(setRecorte).catch(() => setRecorte(null))
  }, [])

  if (!project || !view) return null
  const graficos = view.graficos ?? []
  const camadas = view.camadas ?? []
  const transicoes = view.transicoes ?? []
  const cenas = (view as any).cenas ?? []
  const doClaude = [...graficos, ...camadas, ...transicoes, ...cenas]
    .filter((x: any) => x.origem === 'claude').length

  const itens = [
    ...cenas.map((c: any) => ({ kind: 'cena', id: c.id, t: c.out_start, fim: c.out_end,
      rotulo: `Cena: ${NOME_CENA[c.tipo] ?? c.tipo}`, extra: c.lado || '', claude: c.origem === 'claude' })),
    ...graficos.map((g: any) => ({ kind: 'grafico', id: g.id, t: g.out_start, fim: g.out_end,
      rotulo: g.tipo === 'logo' ? `Logo: ${g.logo}` : `${NOME_TIPO[g.tipo] ?? g.tipo}${g.texto ? `: ${g.texto}` : ''}`,
      extra: g.camada === 'atras' ? 'atrás da pessoa' : '', claude: g.origem === 'claude' })),
    ...camadas.map((c: any) => ({ kind: 'camada', id: c.id, t: c.out_start, fim: c.out_end,
      rotulo: NOME_EFEITO[c.efeito] ?? c.efeito, extra: '', claude: c.origem === 'claude' })),
    ...transicoes.map((x: any) => ({ kind: 'transicao', id: x.id, t: x.emenda ?? 0, fim: null,
      rotulo: `Transição: ${NOME_TRANS[x.tipo] ?? x.tipo}`, extra: '', claude: x.origem === 'claude' })),
  ].sort((a, b) => a.t - b.t)

  const por = async (tipo: 'graficos' | 'camadas' | 'cenas', dados: any, dura = 3) => {
    snapshot()
    const t = getPlayhead()
    try {
      const r = await api.posCriar(project.id, tipo, { ...dados, out_start: t, out_end: t + dura })
      await onChanged()
      onSelect(tipo === 'graficos' ? 'grafico' : tipo === 'cenas' ? 'cena' : 'camada', r.item.id)
    } catch (e: any) {
      toast('warn', 'Não deu para pôr', String(e.message ?? e))
    }
  }

  const transicao = async () => {
    snapshot()
    try {
      const r = await api.posCriar(project.id, 'transicoes', { tipo: 'zoom', tempo: getPlayhead() })
      await onChanged()
      onSelect('transicao', r.item.id)
    } catch (e: any) {
      toast('warn', 'Sem emenda para a transição', String(e.message ?? e))
    }
  }

  const baixar = async () => {
    setBaixando(true)
    try {
      setRecorte(await api.recorteBaixar())
      toast('ok', 'Recorte da pessoa pronto', 'Camadas e texto atrás da pessoa já funcionam.')
    } catch (e: any) {
      toast('warn', 'Não deu para baixar o modelo', String(e.message ?? e))
    } finally { setBaixando(false) }
  }

  const tirarDoClaude = async () => {
    snapshot()
    try {
      await api.posTirar(project.id, { tudo: true, origem: 'claude' })
      await onChanged()
    } catch (e: any) {
      toast('warn', 'Não deu para tirar', String(e.message ?? e))
    }
  }

  return (
    <div className="p-3 space-y-3" data-pos-panel="1">
      <div>
        <h2 className="text-sm font-semibold text-slate-100">Pós-edição</h2>
        <p className="text-[11px] text-slate-400 leading-snug">
          O que vai por cima do corte: títulos, telas de tópico, listas, números,
          transições e as camadas (a pessoa separada do fundo). O Claude faz isso
          pelo MCP{view.editor === 'claude' ? ' — ele é o editor deste vídeo' : ''};
          aqui você confere e ajusta. Sai no mesmo encode, sem perder qualidade.
        </p>
      </div>

      <div className="card p-2 text-[11px]" data-recorte={recorte?.pronto ? 'pronto' : 'falta'}>
        {recorte?.pronto ? (
          <span className="text-emerald-300">Recorte da pessoa pronto (roda nesta máquina).</span>
        ) : recorte?.runtime ? (
          <div className="flex items-center gap-2">
            <span className="text-slate-300 flex-1">
              O recorte da pessoa baixa um modelo de {recorte.tamanho_mb} MB uma vez (sozinho no
              primeiro uso, ou agora).</span>
            <button className="btn btn-xs" disabled={baixando} onClick={baixar}>
              {baixando ? 'baixando…' : 'baixar'}</button>
          </div>
        ) : (
          <span className="text-amber-300">
            Para camadas e texto atrás da pessoa, rode o instalar.bat de novo (falta o onnxruntime).
            Os gráficos funcionam sem ele.</span>
        )}
      </div>

      <MarcaDoVideo projeto={project.id} marcaDoPlano={(view as any).marca ?? ''} />

      <button className="btn btn-primary w-full" data-acao="claude-pos"
              disabled={!!claudeRodando}
              onClick={async () => {
                try {
                  const job = await api.claudePedir(project.id, '', 'pos')
                  setState({ activeJob: job })
                  toast('info', 'O Claude está fazendo a pós-edição',
                    'Títulos, telas, transições e camadas por cima da edição. Acompanhe no topo.')
                } catch (e: any) {
                  toast('warn', 'Não deu para chamar o Claude', String(e.message ?? e))
                }
              }}>
        {claudeRodando ? 'o Claude está trabalhando…' : 'O Claude faz a pós-edição (1 clique)'}
      </button>

      <div>
        <span className="label">pôr no cursor</span>
        <div className="flex flex-wrap gap-1.5">
          {RAPIDOS.map((r) => (
            <button key={r.rotulo} className="btn btn-xs" onClick={() => por(r.tipo, r.dados, r.dura)}>
              + {r.rotulo}</button>
          ))}
          <button className="btn btn-xs" onClick={transicao}>+ transição na emenda</button>
        </div>
      </div>

      <div>
        <div className="flex items-center gap-2 mb-1">
          <span className="label mb-0 flex-1">no vídeo ({itens.length})</span>
          {doClaude > 0 && (
            <button className="btn btn-xs text-red-300" onClick={tirarDoClaude}>
              tirar o que o Claude pôs ({doClaude})</button>
          )}
        </div>
        {!itens.length && (
          <p className="text-[11px] text-slate-500">
            Nada ainda. Peça ao Claude: “faz a pós-edição desse vídeo”, ou use os botões acima.</p>
        )}
        <ul className="space-y-1">
          {itens.map((i) => (
            <li key={i.id}>
              <button className="w-full text-left card px-2 py-1.5 hover:border-amber-600/60"
                      data-pos-item={i.kind}
                      onClick={() => { setPlayhead(i.t); onSelect(i.kind, i.id) }}>
                <span className="text-[10px] text-slate-500 mr-2">
                  {timecode(i.t)}{i.fim != null ? `–${timecode(i.fim)}` : ''}</span>
                <span className="text-xs text-slate-200">{i.rotulo}</span>
                {i.extra && <span className="text-[10px] text-cyan-300 ml-1">({i.extra})</span>}
                {i.claude && <span className="text-[10px] text-amber-300 ml-1">Claude</span>}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}


/**
 * A MARCA DESTE VÍDEO: o kit (nome, cores, logos) que o Claude e os gráficos
 * usam, os logos disponíveis — os da marca e os que você pôs (Airbnb,
 * Booking…) — e a habilidade de motion para o seu app Claude.
 */
function MarcaDoVideo({ projeto, marcaDoPlano }: { projeto: string; marcaDoPlano: string }) {
  const [m, setM] = useState<any>(null)
  const [escolha, setEscolha] = useState(marcaDoPlano)
  const carregar = () => api.marca().then(setM).catch(() => setM(null))
  useEffect(() => { carregar() }, [])
  useEffect(() => { setEscolha(marcaDoPlano) }, [marcaDoPlano])
  if (!m) return null
  const kit = escolha === '-' ? null
    : (escolha ? (m.lista ?? []).find((k: any) => k.slug === escolha) : m.kit)
  const cores: [string, string][] = kit && m.kit && (!escolha || escolha === m.kit.slug)
    ? Object.entries(m.kit.cores ?? {}).slice(0, 5) as [string, string][] : []

  const trocar = async (slug: string) => {
    setEscolha(slug)
    try { await api.projetoMarca(projeto, slug) } catch (e: any) {
      toast('warn', 'Não deu para trocar a marca', String(e.message ?? e))
    }
  }
  const porLogo = async () => {
    try {
      const r = await api.escolher('image', 'Escolher o PNG do logo (fundo transparente)')
      if (r.cancelado) return
      const nome = window.prompt('Nome do logo (ex.: airbnb, booking):',
        String(r.path ?? '').split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') ?? '') ?? ''
      const res = await api.marcaLogo(r.path, nome)
      setM(res)
      toast('ok', `Logo "${res.nome}" pronto`, 'O Claude e o "+ logo" já podem usar.')
    } catch (e: any) {
      toast('warn', 'Não deu para pôr o logo', String(e.message ?? e))
    }
  }
  const instalar = async () => {
    try {
      const r = await api.claudeHabilidade()
      if (r.ok) toast('ok', 'Habilidade instalada no seu Claude', `em ${r.onde}`)
      else toast('warn', 'Não deu para instalar', r.motivo)
    } catch (e: any) {
      toast('warn', 'Não deu para instalar', String(e.message ?? e))
    }
  }
  return (
    <div className="card p-2 space-y-1.5 text-[11px]" data-marca-do-video={kit?.slug ?? 'nenhuma'}>
      <div className="flex items-center gap-2">
        <span className="text-slate-300 flex-1">Marca deste vídeo</span>
        <select className="field text-xs py-0.5" value={escolha} onChange={(e) => trocar(e.target.value)}>
          <option value="">{m.kit ? `a do programa (${m.kit.nome})` : 'a do programa (nenhuma)'}</option>
          {(m.lista ?? []).map((k: any) => <option key={k.slug} value={k.slug}>{k.nome}</option>)}
          <option value="-">nenhuma</option>
        </select>
      </div>
      {cores.length > 0 && (
        <div className="flex items-center gap-1">
          {cores.map(([nome, cor]) => (
            <span key={nome} title={`${nome} ${cor}`} className="w-4 h-4 rounded"
                  style={{ background: cor, border: '1px solid #ffffff22' }} />
          ))}
          <span className="text-slate-500 ml-1">{m.kit?.fonte?.texto}</span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        {Object.keys(m.logos ?? {}).map((n) => (
          <span key={n} className="chip border-line text-slate-300 flex items-center gap-1" data-logo={n}>
            <img src={api.marcaLogoUrl(n)} alt="" className="h-4 w-auto" />{n}
            {!m.logos[n].da_marca && (
              <button className="text-red-300" title="tirar da biblioteca"
                      onClick={async () => setM(await api.marcaLogoApagar(n))}>×</button>
            )}
          </span>
        ))}
        <button className="btn btn-xs" onClick={porLogo} data-acao="por-logo">+ pôr logo (PNG)</button>
      </div>
      <p className="text-slate-500 leading-snug">
        Logos de plataforma (Airbnb, Booking) você põe aqui — o Sharkcut não traz marca dos
        outros. Pela regra da marca, eles nunca aparecem ao lado do seu logo.
      </p>
      <button className="btn btn-xs w-full" onClick={instalar} data-acao="instalar-habilidade"
              title="copia o manual de motion do Sharkcut para as skills do Claude Code desta máquina">
        instalar a habilidade de motion no seu app Claude
      </button>
    </div>
  )
}
