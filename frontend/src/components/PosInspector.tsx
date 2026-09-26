import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { toast, useStore } from '../state/store'

export const TIPOS_GRAFICO: [string, string][] = [
  ['titulo', 'Título'], ['tela', 'Tela de tópico'], ['lista', 'Lista'],
  ['destaque', 'Destaque'], ['numero', 'Número'], ['texto', 'Texto'],
  ['nome', 'Nome (lower third)'], ['seta', 'Seta'], ['circulo', 'Círculo'],
  ['barra', 'Barra de progresso'],
]
const ESTILOS: [string, string][] = [
  ['escuro', 'Escuro'], ['claro', 'Claro'], ['neon', 'Neon'], ['marca', 'Cor da marca'],
  ['limpo', 'Sem fundo'],
]
const ENTRADAS: [string, string][] = [
  ['pop', 'Pop'], ['slide', 'Desliza'], ['subir', 'Sobe'], ['3d', 'Giro 3D'],
  ['digitar', 'Digitando'], ['fade', 'Aparece'],
]
const SAIDAS: [string, string][] = [
  ['fade', 'Some'], ['slide', 'Desliza'], ['pop', 'Encolhe'], ['corte', 'Corte seco'],
]
export const EFEITOS_CAMADA: [string, string][] = [
  ['desfoque', 'Fundo desfocado'], ['escurecer', 'Holofote (fundo escuro)'],
  ['parallax', '3D (a pessoa salta)'], ['recorte', 'Pessoa recortada'],
]
export const TIPOS_TRANSICAO: [string, string][] = [
  ['zoom', 'Zoom'], ['chicote', 'Chicote'], ['flash', 'Flash'], ['glitch', 'Glitch'],
  ['desfoque', 'Desfoque'], ['luz', 'Luz'], ['giro', 'Giro'],
]

const COLECAO: Record<string, 'graficos' | 'camadas' | 'transicoes'> = {
  grafico: 'graficos', camada: 'camadas', transicao: 'transicoes',
}

/**
 * O ITEM DA PÓS-EDIÇÃO CLICADO NO TRILHO: um gráfico, uma camada ou uma
 * transição. Tudo que o Claude pôs pelo MCP dá para ajustar aqui — e o botão
 * "ver o quadro" mostra o encode de verdade parado naquele instante, não uma
 * imitação: é o mesmo comando que gera o arquivo.
 */
export default function PosInspector({ kind, id, onChanged, snapshot, onClose }: {
  kind: string; id: string
  onChanged: () => Promise<any>; snapshot: () => void; onClose: () => void
}) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const lista = (view as any)?.[COLECAO[kind]] ?? []
  const item = lista.find((x: any) => x.id === id)
  const [d, setD] = useState<any>(null)
  const [itensTxt, setItensTxt] = useState('')
  const [quadro, setQuadro] = useState<string | null>(null)
  const [carregando, setCarregando] = useState(false)
  const [salvando, setSalvando] = useState(false)

  useEffect(() => {
    if (!item) return
    setD({ ...item, dura: +((item.out_end ?? 0) - (item.out_start ?? 0)).toFixed(2) })
    setItensTxt((item.itens ?? []).map((i: any) => (typeof i === 'string' ? i : i.texto)).join('\n'))
    setQuadro(null)
  }, [item?.id, item?.out_start, item?.out_end, JSON.stringify(item ?? {})])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!project || !item || !d) return null
  const set = (k: string, v: any) => setD((x: any) => ({ ...x, [k]: v }))
  const emenda = kind === 'transicao'
    ? (view?.transicoes ?? []).find((x: any) => x.id === id)?.emenda ?? null : null
  const meio = kind === 'transicao' ? (emenda ?? 0)
    : Math.min(d.out_start + Math.max(0.2, d.dura) - 0.05, d.out_start + Math.min(1.2, d.dura / 2))

  const aplicar = async () => {
    snapshot()
    setSalvando(true)
    try {
      let dados: any
      if (kind === 'grafico') {
        const antigos = item.itens ?? []
        const itens = itensTxt.split('\n').map((t) => t.trim()).filter(Boolean)
          .map((t, i) => {
            const velho = antigos[i]
            return velho && typeof velho === 'object' && velho.em != null
              ? { texto: t, em: velho.em } : t
          })
        dados = {
          tipo: d.tipo, texto: d.texto, subtexto: d.subtexto, itens,
          out_start: Math.max(0, +d.out_start), out_end: Math.max(0, +d.out_start) + Math.max(0.3, +d.dura),
          x: +d.x, y: +d.y, tamanho: +d.tamanho, estilo: d.estilo, cor: d.cor || '',
          entrada: d.entrada, saida: d.saida, camada: d.camada,
          numero: +d.numero, prefixo: d.prefixo, sufixo: d.sufixo, angulo: +d.angulo,
        }
      } else if (kind === 'camada') {
        dados = { efeito: d.efeito, forca: +d.forca, out_start: Math.max(0, +d.out_start),
                  out_end: Math.max(0, +d.out_start) + Math.max(0.3, +d.dura) }
      } else {
        dados = { tipo: d.tipo, duracao: +d.duracao }
      }
      await api.posMudar(project.id, COLECAO[kind], id, dados)
      await onChanged()
      toast('ok', 'Pós-edição ajustada', 'Sai no próximo vídeo gerado — só o trecho dele é refeito.')
    } catch (e: any) {
      toast('warn', 'Não deu para ajustar', String(e.message ?? e))
    } finally { setSalvando(false) }
  }

  const apagar = async () => {
    snapshot()
    try {
      await api.posApagar(project.id, id)
      onClose()
      await onChanged()
    } catch (e: any) {
      toast('warn', 'Não deu para apagar', String(e.message ?? e))
    }
  }

  const titulo = kind === 'grafico' ? 'Gráfico' : kind === 'camada' ? 'Camada' : 'Transição'
  const pedeItens = d.tipo === 'lista' || d.tipo === 'tela'
  const pedeNumero = d.tipo === 'numero' || d.tipo === 'barra'

  return (
    <section className="card p-3 m-3 space-y-2.5 border-amber-600/60" data-pos-inspector={kind}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-xs font-semibold text-amber-300 uppercase tracking-wide">{titulo}</h3>
          {item.origem === 'claude' && (
            <p className="text-[10px] text-slate-400">posto pelo Claude na pós-edição</p>
          )}
        </div>
        <button className="btn btn-xs" onClick={onClose} title="fechar">×</button>
      </div>

      {kind === 'grafico' && (
        <>
          <label className="block">
            <span className="label">tipo</span>
            <select className="field w-full text-xs py-1" value={d.tipo} data-campo="tipo"
                    onChange={(e) => set('tipo', e.target.value)}>
              {TIPOS_GRAFICO.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="label">{d.tipo === 'nome' ? 'nome' : d.tipo === 'numero' ? 'legenda do número' : 'texto'}</span>
            <textarea className="field w-full text-xs py-1" rows={2} value={d.texto ?? ''}
                      data-campo="texto" onChange={(e) => set('texto', e.target.value)} />
          </label>
          {['titulo', 'tela', 'texto', 'nome', 'barra'].includes(d.tipo) && (
            <label className="block">
              <span className="label">{d.tipo === 'nome' ? 'função' : 'linha de baixo'}</span>
              <input className="field w-full text-xs py-1" value={d.subtexto ?? ''}
                     onChange={(e) => set('subtexto', e.target.value)} />
            </label>
          )}
          {pedeItens && (
            <label className="block">
              <span className="label">tópicos (um por linha)</span>
              <textarea className="field w-full text-xs py-1" rows={3} value={itensTxt}
                        data-campo="itens" onChange={(e) => setItensTxt(e.target.value)} />
            </label>
          )}
          {(pedeNumero || d.tipo === 'tela') && (
            <div className="grid grid-cols-3 gap-2">
              {pedeNumero && (
                <label className="block">
                  <span className="label">{d.tipo === 'barra' ? '% cheio' : 'número'}</span>
                  <input className="field w-full text-xs py-1" type="number" value={d.numero ?? 0}
                         onChange={(e) => set('numero', e.target.value)} />
                </label>
              )}
              <label className="block">
                <span className="label">{d.tipo === 'tela' ? 'rótulo' : 'antes'}</span>
                <input className="field w-full text-xs py-1" value={d.prefixo ?? ''}
                       placeholder={d.tipo === 'tela' ? 'PARTE 1' : 'R$ '}
                       onChange={(e) => set('prefixo', e.target.value)} />
              </label>
              {pedeNumero && (
                <label className="block">
                  <span className="label">depois</span>
                  <input className="field w-full text-xs py-1" value={d.sufixo ?? ''} placeholder="%"
                         onChange={(e) => set('sufixo', e.target.value)} />
                </label>
              )}
            </div>
          )}
          {d.tipo === 'seta' && (
            <label className="block">
              <span className="label">aponta para (graus: 0 direita, 90 baixo)</span>
              <input className="field w-full text-xs py-1" type="number" step={15} value={d.angulo ?? 0}
                     onChange={(e) => set('angulo', e.target.value)} />
            </label>
          )}
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="label">horizontal</span>
              <input type="range" min={0} max={1} step={0.01} value={d.x} className="w-full"
                     onChange={(e) => set('x', +e.target.value)} />
            </label>
            <label className="block">
              <span className="label">vertical</span>
              <input type="range" min={0} max={1} step={0.01} value={d.y} className="w-full"
                     onChange={(e) => set('y', +e.target.value)} />
            </label>
          </div>
          <label className="block">
            <span className="label">tamanho ({(+d.tamanho).toFixed(1)}×)</span>
            <input type="range" min={0.4} max={2.5} step={0.1} value={d.tamanho} className="w-full"
                   onChange={(e) => set('tamanho', +e.target.value)} />
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="label">estilo</span>
              <select className="field w-full text-xs py-1" value={d.estilo}
                      onChange={(e) => set('estilo', e.target.value)}>
                {ESTILOS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label">cor de destaque</span>
              <div className="flex items-center gap-1">
                <input type="color" value={d.cor || '#ffc400'} className="h-7 w-10 bg-transparent"
                       onChange={(e) => set('cor', e.target.value)} />
                {d.cor && <button className="btn btn-xs" onClick={() => set('cor', '')}>padrão</button>}
              </div>
            </label>
            <label className="block">
              <span className="label">entra</span>
              <select className="field w-full text-xs py-1" value={d.entrada}
                      onChange={(e) => set('entrada', e.target.value)}>
                {ENTRADAS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="label">sai</span>
              <select className="field w-full text-xs py-1" value={d.saida}
                      onChange={(e) => set('saida', e.target.value)}>
                {SAIDAS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          </div>
          <label className="flex items-center gap-2 text-[11px] text-slate-300">
            <input type="checkbox" checked={d.camada === 'atras'} data-campo="atras"
                   onChange={(e) => set('camada', e.target.checked ? 'atras' : 'frente')} />
            atrás da pessoa (o texto passa por trás dela)
          </label>
        </>
      )}

      {kind === 'camada' && (
        <>
          <label className="block">
            <span className="label">efeito</span>
            <select className="field w-full text-xs py-1" value={d.efeito}
                    onChange={(e) => set('efeito', e.target.value)}>
              {EFEITOS_CAMADA.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="label">força ({(+d.forca).toFixed(1)})</span>
            <input type="range" min={0} max={1} step={0.05} value={d.forca} className="w-full"
                   onChange={(e) => set('forca', +e.target.value)} />
          </label>
        </>
      )}

      {kind === 'transicao' && (
        <div className="grid grid-cols-2 gap-2">
          <label className="block">
            <span className="label">tipo</span>
            <select className="field w-full text-xs py-1" value={d.tipo}
                    onChange={(e) => set('tipo', e.target.value)}>
              {TIPOS_TRANSICAO.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="label">duração (s)</span>
            <input className="field w-full text-xs py-1" type="number" step={0.1} min={0.1} max={1.2}
                   value={d.duracao} onChange={(e) => set('duracao', e.target.value)} />
          </label>
          {emenda != null && (
            <p className="col-span-2 text-[10px] text-slate-500">na emenda de {timecode(emenda)}</p>
          )}
        </div>
      )}

      {kind !== 'transicao' && (
        <div className="grid grid-cols-2 gap-2">
          <label className="block">
            <span className="label">começa em (s)</span>
            <input className="field w-full text-xs py-1" type="number" step={0.1} min={0}
                   value={d.out_start} data-campo="inicio"
                   onChange={(e) => set('out_start', +e.target.value)} />
          </label>
          <label className="block">
            <span className="label">dura (s)</span>
            <input className="field w-full text-xs py-1" type="number" step={0.1} min={0.3}
                   value={d.dura} data-campo="dura"
                   onChange={(e) => set('dura', +e.target.value)} />
          </label>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button className="btn btn-primary btn-xs" disabled={salvando} onClick={aplicar}
                data-acao="aplicar">
          {salvando ? 'aplicando…' : 'aplicar'}
        </button>
        <button className="btn btn-xs" data-acao="ver-quadro"
                onClick={() => { setCarregando(true); setQuadro(api.posQuadroUrl(project.id, meio)) }}>
          ver o quadro exato
        </button>
        <button className="btn btn-xs text-red-300" onClick={apagar} data-acao="apagar">apagar</button>
      </div>
      {quadro && (
        <div className="space-y-1">
          {carregando && <p className="text-[10px] text-slate-400">renderizando o quadro de {timecode(meio)}…</p>}
          <img src={quadro} alt="quadro do vídeo final" className="w-full rounded bg-black"
               data-quadro-exato="1"
               onLoad={() => setCarregando(false)}
               onError={() => { setCarregando(false); toast('warn', 'O quadro não saiu', 'Veja se o vídeo já foi editado.') }} />
          <p className="text-[10px] text-slate-500">
            o encode de verdade em {timecode(meio)} — ajuste e aplique para ver de novo
          </p>
        </div>
      )}
    </section>
  )
}
