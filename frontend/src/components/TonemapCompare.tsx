import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { toast } from '../state/store'

interface Props {
  projectId: string
  mediaId: string
  fit: any
  time: number
  mainTime: number
  onApply: (fit: any) => void
}

/**
 * Comparação lado a lado da conversão HDR → SDR (Parte 7.1).
 *
 * Num teste de ida e volta controlado (SDR → HLG/BT.2020 → de volta) a
 * conversão só de transferência devolve o original com 0,22 de erro em 255,
 * enquanto a cadeia com hable erra 45. Por isso o padrão é a transferência —
 * mas o material de celular varia, então aqui dá para ver e escolher.
 */
export default function TonemapCompare({ projectId, mediaId, fit, time,
                                         mainTime, onApply }: Props) {
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [npl, setNpl] = useState(fit?.npl ?? 100)
  const [operator, setOperator] = useState(fit?.tonemap_operator ?? 'hable')
  const [brightness, setBrightness] = useState(fit?.brightness ?? 0)
  const [saturation, setSaturation] = useState(fit?.saturation ?? 1)
  const [contrast, setContrast] = useState(fit?.contrast ?? 1)

  const load = async () => {
    setBusy(true)
    try {
      setData(await api.tonemapPreview(projectId, mediaId, {
        t: time, npl, operator, brightness, saturation, contrast,
        main_time: mainTime,
      }))
    } catch (e: any) {
      toast('error', 'Falha na comparação', String(e.message ?? e))
    } finally { setBusy(false) }
  }

  useEffect(() => { load() }, [mediaId])

  return (
    <div className="border border-line rounded-md p-2.5 mt-2">
      <div className="flex items-center gap-2 mb-2">
        <h4 className="text-xs font-medium text-slate-300">Conversão HDR → SDR</h4>
        <button className="btn btn-xs ml-auto" disabled={busy} onClick={load}>
          {busy ? 'renderizando…' : 'atualizar'}
        </button>
      </div>

      <div className="grid grid-cols-5 gap-2 mb-2">
        <label className="block">
          <span className="label">npl</span>
          <input className="field" type="number" value={npl}
                 onChange={(e) => setNpl(+e.target.value)} />
        </label>
        <label className="block">
          <span className="label">operador</span>
          <select className="field" value={operator}
                  onChange={(e) => setOperator(e.target.value)}>
            {['hable', 'mobius', 'reinhard', 'gamma', 'clip', 'linear']
              .map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </label>
        <label className="block">
          <span className="label">brilho</span>
          <input className="field" type="number" step={0.02} value={brightness}
                 onChange={(e) => setBrightness(+e.target.value)} />
        </label>
        <label className="block">
          <span className="label">saturação</span>
          <input className="field" type="number" step={0.05} value={saturation}
                 onChange={(e) => setSaturation(+e.target.value)} />
        </label>
        <label className="block">
          <span className="label">contraste</span>
          <input className="field" type="number" step={0.05} value={contrast}
                 onChange={(e) => setContrast(+e.target.value)} />
        </label>
      </div>

      <div className="grid grid-cols-4 gap-2">
        {data?.main && (
          <figure className="text-[10px]">
            <img src={data.main.image} alt="vídeo principal"
                 className="w-full rounded border border-line" />
            <figcaption className="mt-1 text-slate-400">
              principal · luma {data.main.mean_luma}
            </figcaption>
          </figure>
        )}
        {(data?.variants ?? []).map((v: any, i: number) => (
          <figure key={i} className="text-[10px]">
            {v.image
              ? <img src={v.image} alt={v.label}
                     className="w-full rounded border border-line" />
              : <div className="w-full aspect-[9/16] rounded border border-red-900
                                grid place-items-center text-red-400 p-2">
                  {v.error}
                </div>}
            <figcaption className="mt-1">
              <span className="text-slate-300">{v.label}</span>
              {v.mean_luma !== undefined && (
                <span className={`ml-1 font-mono ${
                  data.main && Math.abs(v.mean_luma - data.main.mean_luma) < 8
                    ? 'text-emerald-400' : 'text-slate-500'}`}>
                  luma {v.mean_luma}
                </span>
              )}
              <span className="block text-slate-500 mt-0.5">{v.note}</span>
              {i > 0 && (
                <button className="btn btn-xs mt-1 w-full"
                        onClick={() => onApply({
                          tonemap: true,
                          tonemap_mode: i === 1 ? 'transferencia' : 'operador',
                          npl, tonemap_operator: operator,
                          brightness, saturation, contrast,
                        })}>
                  usar esta
                </button>
              )}
            </figcaption>
          </figure>
        ))}
      </div>
      {data?.main && (
        <p className="hint mt-2">
          Escolha a que deixar o <b>luma</b> mais perto do vídeo principal
          ({data.main.mean_luma}). É a diferença de luma que faz o inserto piscar
          na emenda.
        </p>
      )}
    </div>
  )
}
