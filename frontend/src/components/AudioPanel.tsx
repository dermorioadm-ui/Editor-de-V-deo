import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { toast, useStore } from '../state/store'

interface Props { onChanged: () => Promise<any> }

export default function AudioPanel({ onChanged }: Props) {
  const project = useStore((s) => s.project)
  const [analysis, setAnalysis] = useState<any>(null)
  const [preview, setPreview] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [denoise, setDenoise] = useState(false)
  const [strength, setStrength] = useState(1.0)
  const [params, setParams] = useState<any>(project?.plan?.audio ?? {})
  const [deesser, setDeesser] = useState<any>(null)

  useEffect(() => { setParams(project?.plan?.audio ?? {}) }, [project?.plan?.audio])
  useEffect(() => {
    if (!project) return
    setBusy(true)
    api.audioAnalysis(project.id)
      .then((a) => { setAnalysis(a); setDenoise(a.denoise_enabled) })
      .catch((e) => toast('error', 'Falha ao analisar o áudio', String(e.message ?? e)))
      .finally(() => setBusy(false))
  }, [project?.id])

  if (!project) return null

  const runPreview = async (withDenoise: boolean) => {
    setBusy(true)
    try {
      const res = await api.audioPreview(project.id, {
        ...params,
        denoise_enabled: withDenoise,
        denoise_chain: withDenoise ? (analysis?.proposal?.chain ?? '') : '',
        duration: 20,
      })
      setPreview(res)
      if (res.sibilance_warning) toast('warn', 'Sibilância subiu', res.sibilance_warning)
    } catch (e: any) {
      toast('error', 'Prévia falhou', String(e.message ?? e))
    } finally { setBusy(false) }
  }

  const apply = async () => {
    setBusy(true)
    try {
      await api.params(project.id, {
        audio: {
          ...params,
          denoise_enabled: denoise,
          denoise_chain: denoise ? (analysis?.proposal?.chain ?? '') : '',
        },
      })
      await onChanged()
      toast('ok', 'Cadeia de áudio salva',
        denoise ? 'Limpeza de ruído LIGADA — foi escolha sua.' : undefined)
    } finally { setBusy(false) }
  }

  const before = preview?.before
  const after = preview?.after

  return (
    <div className="p-4 space-y-5 max-w-4xl">
      <section className="card p-3">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">
          Estágio anti-estouro
        </h3>
        <p className="hint mb-3">
          Gravação de celular tem crest factor alto. Normalizar direto para −14 LUFS joga
          os picos acima de 0 dBFS e distorce. Aqui o compressor entra antes do loudnorm.
          Meta: zero amostras acima de −1 dBFS.
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {([['highpass', 'highpass (Hz)', 1], ['comp_threshold', 'threshold (dB)', 1],
             ['comp_ratio', 'ratio', 0.1], ['comp_attack', 'attack (ms)', 1],
             ['comp_release', 'release (ms)', 1], ['comp_knee', 'knee', 0.5],
             ['target_lufs', 'alvo LUFS', 0.5], ['true_peak', 'pico real (dBTP)', 0.1],
             ['lra', 'LRA', 0.5], ['presence_gain', 'realce de presença (dB)', 0.5],
             ['deesser', 'de-esser (0–1)', 0.05]] as const).map(([key, label, step]) => (
            <label key={key} className="block">
              <span className="label">{label}</span>
              <input className="field" type="number" step={step}
                     value={params[key] ?? 0}
                     onChange={(e) => setParams({ ...params, [key]: +e.target.value })} />
            </label>
          ))}
        </div>
        <div className="font-mono text-[10px] text-slate-500 mt-3 break-all
                        bg-ink-900 rounded p-2 border border-line">
          {analysis?.chain}
        </div>
      </section>

      <section className="card p-3">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Limpeza de ruído
          </h3>
          <span className="chip border-line text-slate-500">desligada por padrão</span>
          <label className="ml-auto flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={denoise}
                   onChange={(e) => setDenoise(e.target.checked)} />
            ligar
          </label>
        </div>
        <p className="hint mb-3">
          Só entra por escolha explícita e depois de você ouvir o A/B. Quando ligada, o
          tratamento é direcionado ao que foi medido — não um denoise genérico.
        </p>

        {analysis?.noise?.available && (
          <div className="text-xs space-y-1.5">
            <div className="flex gap-4">
              <span>SNR atual: <b className="font-mono">{analysis.snr} dB</b></span>
              <span>sibilância: <b className="font-mono">{analysis.sibilance}</b></span>
              <span>ruído medido em <b className="font-mono">
                {analysis.noise.noise_seconds} s</b> de silêncio</span>
            </div>
            {analysis.noise.hum && (
              <p className="text-amber-300">
                hum de rede detectado em {analysis.noise.hum.base_hz} Hz
                ({analysis.noise.hum.score_db} dB acima da mediana)
              </p>
            )}
            {analysis.noise.resonances?.length > 0 && (
              <div>
                <span className="text-slate-400">ressonâncias:</span>
                <ul className="mt-0.5 space-y-0.5">
                  {analysis.noise.resonances.slice(0, 4).map((r: any, i: number) => (
                    <li key={i} className="font-mono text-[11px] text-slate-400">
                      {r.low.toFixed(0)}–{r.high.toFixed(0)} Hz ·
                      +{r.over_median.toFixed(1)} dB
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {analysis.proposal?.steps?.length > 0 && (
              <div className="mt-2">
                <span className="text-slate-400">tratamento proposto:</span>
                <ul className="mt-0.5 space-y-0.5">
                  {analysis.proposal.steps.map((s: any, i: number) => (
                    <li key={i} className="text-[11px] text-slate-400">• {s.detail}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
        {analysis && !analysis.noise?.available && (
          <p className="hint">{analysis.noise?.reason}</p>
        )}
      </section>

      <section className="card p-3">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Prévia A/B
          </h3>
          <div className="ml-auto flex gap-1.5">
            <button className="btn btn-xs" disabled={busy}
                    onClick={() => runPreview(false)}>
              medir só a cadeia
            </button>
            <button className="btn btn-xs" disabled={busy || !analysis?.proposal?.chain}
                    onClick={() => runPreview(true)}>
              medir com limpeza
            </button>
            <button className="btn btn-xs" disabled={busy}
                    onClick={async () => {
                      setBusy(true)
                      try {
                        const r = await api.calibrateDeesser(project.id, {
                          presence_gain: params.presence_gain ?? 0,
                          duration: 20,
                        })
                        setDeesser(r)
                        setParams({ ...params, deesser: r.deesser })
                        await onChanged()
                        toast(r.needed ? 'ok' : 'info',
                          `de-esser ${r.deesser}`, r.message)
                      } catch (e: any) {
                        toast('error', 'Calibração falhou', String(e.message ?? e))
                      } finally { setBusy(false) }
                    }}>
              calibrar de-esser
            </button>
            <button className="btn btn-xs btn-primary" disabled={busy} onClick={apply}>
              aplicar
            </button>
          </div>
        </div>
        {!preview && <p className="hint">Rode a medição antes de aplicar qualquer coisa.</p>}
        {deesser && (
          <div className="mb-3 text-xs border border-line rounded p-2">
            <p className="text-slate-300">{deesser.message}</p>
            <p className="hint mt-1">
              nível original {deesser.baseline} · com o realce {deesser.without} ·
              depois do de-esser {deesser.after}
            </p>
            <div className="font-mono text-[10px] text-slate-600 mt-1">
              {deesser.history.map((h: any, i: number) => (
                <span key={i} className="mr-3">
                  {h.deesser} → {h.sibilance}
                </span>
              ))}
            </div>
          </div>
        )}
        {preview && (
          <div className="space-y-3">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 text-left">
                  <th className="font-normal pb-1">medida</th>
                  <th className="font-normal pb-1">antes</th>
                  <th className="font-normal pb-1">depois</th>
                  <th className="font-normal pb-1">alvo</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {preview.comparison.checks.map((c: any) => (
                  <tr key={c.label} className="border-t border-line">
                    <td className="py-1 font-sans text-slate-300">{c.label}</td>
                    <td>{c.before}</td>
                    <td className={c.ok ? 'text-emerald-400' : 'text-red-400'}>{c.after}</td>
                    <td className="text-slate-500">{c.target ?? '—'}</td>
                  </tr>
                ))}
                <tr className="border-t border-line">
                  <td className="py-1 font-sans text-slate-300">SNR (dB)</td>
                  <td>{preview.snr_before}</td>
                  <td className={preview.snr_after >= preview.snr_before
                    ? 'text-emerald-400' : 'text-red-400'}>{preview.snr_after}</td>
                  <td className="text-slate-500">maior é melhor</td>
                </tr>
                <tr className="border-t border-line">
                  <td className="py-1 font-sans text-slate-300">sibilância (p99)</td>
                  <td>{preview.sibilance_before}</td>
                  <td className={preview.sibilance_after <= preview.sibilance_before * 1.05
                    ? 'text-emerald-400' : 'text-amber-400'}>{preview.sibilance_after}</td>
                  <td className="text-slate-500">não pode subir</td>
                </tr>
              </tbody>
            </table>
            {preview.sibilance_warning && (
              <p className="text-xs text-amber-300">{preview.sibilance_warning}</p>
            )}
            <div className="font-mono text-[10px] text-slate-500 break-all
                            bg-ink-900 rounded p-2 border border-line">
              {preview.chain}
            </div>
          </div>
        )}
      </section>

      {project.plan?.music && (
        <section className="card p-3">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">
            Trilha
          </h3>
          <div className="grid grid-cols-4 gap-2">
            {([['gain_db', 'volume (dB)'], ['duck_amount', 'ducking (dB)'],
               ['fade_in', 'fade in (s)'], ['fade_out', 'fade out (s)']] as const)
              .map(([key, label]) => (
                <label key={key} className="block">
                  <span className="label">{label}</span>
                  <input className="field" type="number" step={0.5}
                         defaultValue={project.plan.music[key]}
                         onBlur={async (e) => {
                           await api.setMusic(project.id,
                             { ...project.plan.music, [key]: +e.target.value })
                           await onChanged()
                         }} />
                </label>
              ))}
          </div>
          <label className="flex items-center gap-1.5 text-xs mt-2">
            <input type="checkbox" defaultChecked={project.plan.music.ducking}
                   onChange={async (e) => {
                     await api.setMusic(project.id,
                       { ...project.plan.music, ducking: e.target.checked })
                     await onChanged()
                   }} />
            ducking por sidechain
          </label>
          <button className="btn btn-xs btn-danger mt-2"
                  onClick={async () => {
                    await api.setMusic(project.id, {})
                    await onChanged()
                  }}>remover trilha</button>
        </section>
      )}
    </div>
  )
}
