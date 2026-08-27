import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { bytes } from '../lib/format'

interface Props {
  extensions?: string[]
  onPick: (path: string) => void
  onClose: () => void
  title?: string
}

export default function FileBrowser({ extensions, onPick, onClose, title }: Props) {
  const [data, setData] = useState<any>(null)
  const [path, setPath] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api.browse(path).then((d) => { setData(d); setError('') })
      .catch((e) => setError(String(e.message ?? e)))
  }, [path])

  const matches = (name: string) =>
    !extensions?.length || extensions.some((e) => name.toLowerCase().endsWith(e))

  const entries = (data?.entries ?? []).filter(
    (e: any) => e.dir || matches(e.name))

  return (
    <div className="fixed inset-0 z-40 bg-black/70 flex items-center justify-center p-6"
         onClick={onClose}>
      <div className="card w-full max-w-2xl max-h-[76vh] flex flex-col"
           onClick={(e) => e.stopPropagation()}>
        <div className="px-4 py-3 border-b border-line flex items-center gap-3">
          <h3 className="font-semibold text-sm">{title ?? 'Escolher arquivo'}</h3>
          <span className="text-xs text-slate-500 font-mono truncate flex-1">
            {data?.path ?? '…'}
          </span>
          <button className="btn btn-xs" onClick={onClose}>Fechar</button>
        </div>
        <div className="px-4 py-2 border-b border-line flex gap-2">
          <input className="field font-mono text-xs" value={path}
                 placeholder="cole um caminho e aperte Enter"
                 onChange={(e) => setPath(e.target.value)}
                 onKeyDown={(e) => {
                   if (e.key === 'Enter') setPath((e.target as HTMLInputElement).value)
                 }} />
          {data?.parent && (
            <button className="btn btn-xs" onClick={() => setPath(data.parent)}>
              ↑ subir
            </button>
          )}
        </div>
        {error && <div className="px-4 py-2 text-xs text-red-400">{error}</div>}
        <div className="overflow-auto flex-1 py-1">
          {entries.map((e: any) => (
            <button key={e.path}
                    className="w-full text-left px-4 py-1.5 hover:bg-ink-600
                               flex items-center gap-2 text-sm"
                    onClick={() => (e.dir ? setPath(e.path) : onPick(e.path))}>
              <span className="w-4 text-slate-500">{e.dir ? '▸' : '•'}</span>
              <span className="flex-1 truncate">{e.name}</span>
              {!e.dir && (
                <span className="text-xs text-slate-500">{bytes(e.size)}</span>
              )}
            </button>
          ))}
          {!entries.length && !error && (
            <div className="px-4 py-6 text-sm text-slate-500">Pasta vazia.</div>
          )}
        </div>
      </div>
    </div>
  )
}
