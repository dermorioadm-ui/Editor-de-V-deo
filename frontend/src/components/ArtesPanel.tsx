import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { getPlayhead, toast, useStore } from '../state/store'

export default function ArtesPanel({ onChanged, onSelect, snapshot }: {
  onChanged: () => Promise<any>; onSelect: (kind: string, id: string) => void; snapshot: () => void
}) {
  const project = useStore(s => s.project)
  const view = useStore(s => s.timeline)
  const [catalogo, setCatalogo] = useState<any>(null)
  const [erro, setErro] = useState('')
  const [busy, setBusy] = useState(false)
  const [lado3d, setLado3d] = useState('esquerda')
  const [blender, setBlender] = useState<any>(null)
  useEffect(() => { api.blender().then(setBlender).catch(() => setBlender({ disponivel: false })) }, [])
  useEffect(() => {
    let ativo = true
    setCatalogo(null); setErro('')
    if (project) api.artes(project.id).then(r => { if (ativo) setCatalogo(r) })
      .catch(e => { if (ativo) setErro(String(e.message ?? e)) })
    return () => { ativo = false }
  }, [project?.id])
  if (!project || !view) return null
  const criar = async (modelo: string) => {
    const inicio = Math.max(0, getPlayhead())
    const fim = Math.min(inicio + 6, view.duration)
    if (fim - inicio < 0.3) { toast('warn', 'Mova o cursor para antes do final do vídeo'); return }
    setBusy(true); snapshot()
    try {
      const r = await api.criarArte(project.id, { modelo, inicio, fim,
        nome: catalogo.vetorial.modelos[modelo], origem: 'manual' })
      await onChanged(); onSelect('grafico', r.item.id)
      toast('ok', 'Arte adicionada', 'Ajuste os textos e confira o quadro antes de gerar a prévia.')
    } catch (e: any) { toast('warn', 'Não deu para criar a arte', String(e.message ?? e)) }
    finally { setBusy(false) }
  }
  const objeto3d = async (objeto: string) => {
    const inicio = Math.max(0, getPlayhead())
    if (view.duration - inicio < 1.2) { toast('warn', 'Mova o cursor para antes do final do vídeo'); return }
    setBusy(true); snapshot()
    try {
      await api.objeto3d(project.id, { objeto, inicio, duracao: 3, lado: lado3d, origem: 'manual' })
      toast('ok', `Objeto 3D (${objeto}) no Blender`,
        'Renderiza nesta máquina e entra na linha do tempo quando terminar — acompanhe no rodapé.')
    } catch (e: any) { toast('warn', 'Não deu para criar o objeto 3D', String(e.message ?? e)) }
    finally { setBusy(false) }
  }
  const transicao3d = async () => {
    setBusy(true); snapshot()
    try {
      await api.transicao3d(project.id, { em: Math.max(0, getPlayhead()), duracao: 0.8, origem: 'manual' })
      toast('ok', 'Transição 3D no Blender', 'As faixas da marca tampam a tela no ponto do cursor.')
    } catch (e: any) { toast('warn', 'Não deu para criar a transição 3D', String(e.message ?? e)) }
    finally { setBusy(false) }
  }
  const OBJETOS: [string, string][] = [['casa', 'Casa de temporada'], ['predio', 'Prédio'],
    ['chave', 'Chave'], ['cadeado', 'Cadeado'], ['escudo', 'Escudo'], ['documento', 'Contrato'],
    ['celular', 'Celular'], ['calendario', 'Calendário'], ['check', 'Check'],
    ['estrela', 'Estrela'], ['grafico', 'Gráfico'], ['mala', 'Mala']]
  return <section className="card p-3 space-y-3" data-artes-panel>
    <div>
      <h3 className="text-sm font-semibold text-slate-100">Artes e composição</h3>
      <p className="text-xs text-slate-400 mt-1">Diagramas, tipografia, formas e números com movimentos independentes.
        A IA pode combinar essas peças em cenas próprias.</p>
    </div>
    {erro && <p role="alert" className="text-xs text-amber-300">{erro}</p>}
    <div className="grid grid-cols-2 gap-2">
      {Object.entries(catalogo?.vetorial?.modelos ?? {}).map(([id, nome]) =>
        <button key={id} disabled={busy} onClick={() => criar(id)}
          className="btn text-xs text-left disabled:opacity-40">{String(nome)}</button>)}
    </div>
    <p className="text-[11px] text-slate-500">Exemplos editáveis no ponto do cursor. A escolha de texto e números é sua.</p>
    {catalogo?.blender?.disponivel && <div className="space-y-2 border-t border-line/60 pt-2" data-objetos-3d>
      <div className="flex items-center gap-2">
        <h4 className="text-xs font-semibold text-slate-200">Objeto 3D pronto</h4>
        <select className="field text-xs py-0.5" value={lado3d} onChange={e => setLado3d(e.target.value)}>
          <option value="esquerda">à esquerda</option><option value="direita">à direita</option>
          <option value="centro">no centro</option></select>
      </div>
      <div className="grid grid-cols-3 gap-1.5">
        {OBJETOS.map(([id, nome]) => <button key={id} disabled={busy} data-objeto-3d={id}
          onClick={() => objeto3d(id)} className="btn btn-xs disabled:opacity-40">{nome}</button>)}
      </div>
      <button disabled={busy} data-transicao-3d onClick={transicao3d}
        className="btn btn-xs w-full disabled:opacity-40">Transição 3D no cursor</button>
      <p className="text-[11px] text-slate-500">Modelados no Blender desta máquina, nas cores da marca,
        montando peça por peça. Leva alguns minutos; o vídeo continua editável enquanto isso.</p>
    </div>}
    <div className="flex items-center gap-2 flex-wrap text-[11px]" data-blender-estado={blender?.disponivel ? 'ok' : 'falta'}>
      <span className={blender?.disponivel ? 'text-emerald-300' : 'text-amber-300'}>
        {blender == null ? 'procurando o Blender…'
          : blender.disponivel ? `Blender pronto${blender.versao ? ` · ${blender.versao}` : ''}`
          : 'Blender não encontrado nesta máquina'}</span>
      <button className="btn btn-xs" data-apontar-blender onClick={async () => {
        try {
          const r = await api.escolher('programa', 'Apontar o blender.exe (a pasta onde o Blender está)')
          if (r.cancelado || !r.path) return
          const e = await api.blenderEscolher(r.path)
          setBlender(e)
          toast('ok', 'Blender pronto', `${e.versao || ''} — os objetos, logos e transições 3D já podem sair`)
          api.artes(project.id).then(setCatalogo).catch(() => {})
        } catch (e: any) { toast('warn', 'Não deu para usar esse Blender', String(e.message ?? e)) }
      }}>{blender?.disponivel ? 'trocar o Blender' : 'Apontar o Blender'}</button>
      {blender && !blender.disponivel && <span className="text-slate-500 basis-full">
        Instalou pelo Codex ou em outra pasta? Clique em "Apontar o Blender" e escolha o blender.exe.
        Sem ele, os objetos 3D, os logos 3D do gancho e a transição 3D não saem.</span>}
    </div>
    {catalogo && <p className="text-xs text-slate-400">
      {catalogo.blender.disponivel
        ? 'Blender disponível: o diretor também pode criar objetos e texto 3D com luzes, câmera e transparência.'
        : '3D opcional: instale o Blender para o diretor criar objetos, texto, luzes e câmera. As artes acima já funcionam.'}
    </p>}
  </section>
}
