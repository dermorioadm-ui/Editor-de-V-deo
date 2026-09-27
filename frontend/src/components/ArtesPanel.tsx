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
    {catalogo && <p className="text-xs text-slate-400">
      {catalogo.blender.disponivel
        ? 'Blender disponível: o diretor também pode criar objetos e texto 3D com luzes, câmera e transparência.'
        : '3D opcional: instale o Blender para o diretor criar objetos, texto, luzes e câmera. As artes acima já funcionam.'}
    </p>}
  </section>
}
