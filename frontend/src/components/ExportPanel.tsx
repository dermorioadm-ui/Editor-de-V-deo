import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { mbps, timecode } from '../lib/format'
import { setState, toast, useStore } from '../state/store'

interface Props { onChanged: () => Promise<any> }

export default function ExportPanel({ onChanged }: Props) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const jobs = useStore((s) => s.jobs)
  const [params, setParams] = useState<any>(project?.plan?.export ?? {})
  const [estimate, setEstimate] = useState<any>(null)
  const [result, setResult] = useState<any>(null)
  const [report, setReport] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [presetName, setPresetName] = useState('')
  const [hw, setHw] = useState('')
  const [health, setHealth] = useState<any>(null)

  useEffect(() => { setParams(project?.plan?.export ?? {}) }, [project?.plan?.export])
  useEffect(() => { api.health().then(setHealth).catch(() => {}) }, [])

  // colhe o resultado dos jobs de exportação/validação
  useEffect(() => {
    for (const job of Object.values(jobs)) {
      if (job.status !== 'ok') continue
      if (job.kind === 'exportacao' && job.result?.output && result?.output !== job.result.output) {
        setResult(job.result)
      }
      if (job.kind === 'validacao' && job.result?.output && report?.output !== job.result.output) {
        setReport(job.result)
      }
    }
  }, [jobs])

  if (!project || !view) return null

  const save = async (patch: any) => {
    const next = { ...params, ...patch }
    setParams(next)
    await api.params(project.id, { export: patch })
    await onChanged()
  }

  const run = async (restart = false) => {
    setBusy(true)
    try {
      const job = await api.exportProject(project.id, {
        filename: `${project.name}_editado.mp4`,
        restart,
        hw_encoder: hw || undefined,
      })
      setState({ activeJob: job })
      toast('info', 'Exportando', 'Cada trecho é encodado uma única vez.')
    } finally { setBusy(false) }
  }

  const sourceBitrate = project.info?.v_bitrate || project.info?.bitrate || 0

  return (
    <div className="p-4 space-y-5 max-w-4xl">
      <section className="card p-3">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">
          Parâmetros de saída
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <label className="block">
            <span className="label">Codec</span>
            <select className="field" value={params.codec ?? 'h264'}
                    onChange={(e) => save({ codec: e.target.value })}>
              <option value="h264">H.264</option>
              <option value="h265">H.265 (arquivo menor)</option>
            </select>
          </label>
          <label className="block">
            <span className="label">Preset</span>
            <select className="field" value={params.preset ?? 'medium'}
                    onChange={(e) => save({ preset: e.target.value })}>
              {['ultrafast', 'veryfast', 'fast', 'medium', 'slow', 'slower']
                .map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="label">CRF (menor = melhor)</span>
            <input className="field" type="number" min={8} max={32}
                   value={params.crf ?? 15}
                   onChange={(e) => save({ crf: +e.target.value })} />
          </label>
          <label className="block">
            <span className="label">Áudio</span>
            <select className="field" value={params.audio_bitrate ?? '256k'}
                    onChange={(e) => save({ audio_bitrate: e.target.value })}>
              {['192k', '256k', '320k'].map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="label">Resolução</span>
            <select className="field" value={params.scale ?? 'source'}
                    onChange={(e) => save({ scale: e.target.value })}>
              <option value="source">da fonte (recomendado)</option>
              <option value="720">720 de largura</option>
              <option value="480">480 (prévia rápida)</option>
            </select>
          </label>
          <label className="block">
            <span className="label">Encoder</span>
            <select className="field" value={hw} onChange={(e) => setHw(e.target.value)}>
              <option value="">CPU (libx264/libx265)</option>
              <option value="auto">GPU automático</option>
              {(health?.hw_encoders ?? []).map((h: string) =>
                <option key={h} value={h}>{h}</option>)}
            </select>
          </label>
          <label className="flex items-end gap-1.5 text-xs pb-1.5">
            <input type="checkbox" checked={params.burn_subtitles ?? true}
                   onChange={(e) => save({ burn_subtitles: e.target.checked })} />
            queimar legendas
          </label>
        </div>
        <p className="hint mt-2">
          Resolução e taxa de quadros da fonte são mantidas. O plano é declarativo:
          nenhuma alteração renderizou nada até aqui, e na exportação cada trecho é
          encodado uma única vez, direto do arquivo original.
        </p>
      </section>

      <section className="card p-3">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Bitrate estimado
          </h3>
          <button className="btn btn-xs ml-auto" disabled={busy}
                  onClick={async () => {
                    setBusy(true)
                    try { setEstimate(await api.bitrateEstimate(project.id)) }
                    finally { setBusy(false) }
                  }}>
            estimar (encoda 4 s de amostra)
          </button>
        </div>
        <div className="grid grid-cols-3 gap-3 text-sm">
          <div>
            <div className="label">fonte</div>
            <div className="font-mono">{mbps(sourceBitrate)}</div>
          </div>
          <div>
            <div className="label">estimado</div>
            <div className="font-mono">
              {estimate?.available ? mbps(estimate.estimated_video_bitrate) : '—'}
            </div>
          </div>
          <div>
            <div className="label">variação</div>
            <div className={`font-mono ${estimate?.warn ? 'text-red-400' : 'text-emerald-400'}`}>
              {estimate?.available ? `${-estimate.drop_percent}%` : '—'}
            </div>
          </div>
        </div>
        {estimate?.warn && (
          <p className="mt-2 text-sm text-red-300 font-medium">
            ⚠ {estimate.message}. Baixe o CRF antes de exportar.
          </p>
        )}
      </section>

      <section className="flex items-center gap-2">
        <button className="btn btn-primary px-5 py-2" disabled={busy} onClick={() => run(false)}>
          EXPORTAR
        </button>
        <button className="btn" disabled={busy} onClick={() => run(true)}>
          exportar do zero
        </button>
        <span className="hint">
          A exportação é feita em blocos e retomável: rodar de novo reaproveita os trechos
          que não mudaram.
        </span>
      </section>

      {result && (
        <section className="card p-3">
          <div className="flex items-center gap-2 mb-2">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
              Resultado
            </h3>
            <a className="btn btn-xs ml-auto" href={result.download} download>
              baixar / abrir
            </a>
            <button className="btn btn-xs btn-primary" disabled={busy}
                    onClick={async () => {
                      const job = await api.validate(project.id, result.output)
                      setState({ activeJob: job })
                      toast('info', 'Validando',
                        'Transcreve o resultado e compara palavra a palavra.')
                    }}>
              validar
            </button>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <Metric label="duração" value={timecode(result.duration, true)} />
            <Metric label="bitrate" value={mbps(result.bitrate)} />
            <Metric label="deriva A/V"
                    value={`${(result.drift * 1000).toFixed(1)} ms`}
                    ok={Math.abs(result.drift) < 0.05} />
            <Metric label="trechos" value={String(result.segments?.length ?? 0)} />
          </div>
          <p className="font-mono text-[10px] text-slate-500 mt-2 break-all">
            {result.output}
          </p>
          {result.warnings?.map((w: string, i: number) => (
            <p key={i} className="text-xs text-amber-300 mt-1.5">⚠ {w}</p>
          ))}
          {result.segments?.length > 0 && (
            <details className="mt-2">
              <summary className="text-xs text-slate-400 cursor-pointer">
                desvio medido por bloco (é por isso que a legenda não deriva)
              </summary>
              <div className="mt-1.5 max-h-40 overflow-auto font-mono text-[10px]
                              text-slate-500 space-y-0.5">
                {result.segments.map((s: any) => (
                  <div key={s.index}>
                    #{String(s.index).padStart(3, '0')} {s.kind} ·
                    início {s.out_start.toFixed(3)} s ·
                    medido {s.measured.toFixed(3)} s ·
                    teórico {s.theoretical.toFixed(3)} s ·
                    {' '}{s.delta_ms > 0 ? '+' : ''}{s.delta_ms} ms
                  </div>
                ))}
              </div>
            </details>
          )}
        </section>
      )}

      {report && (
        <section className="card p-3">
          <div className="flex items-center gap-2 mb-2">
            <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
              Validação automática
            </h3>
            <span className={`chip ml-auto ${report.ok
              ? 'border-emerald-700 text-emerald-300'
              : 'border-amber-700 text-amber-300'}`}>
              {report.ok ? 'pode publicar' : 'confira os pontos abaixo'}
            </span>
          </div>

          <div className="grid sm:grid-cols-2 gap-3">
            <Block title="Arquivo" ok={report.container?.ok}>
              <Row k="duração" v={`${report.container.duration} s (esperado ${report.container.expected_duration} s)`} />
              <Row k="resolução" v={`${report.container.width}×${report.container.height} @ ${report.container.fps} fps`} />
              <Row k="bitrate" v={`${mbps(report.container.video_bitrate)} (fonte ${mbps(report.container.source_video_bitrate)})`} />
              <Row k="variação" v={`${-report.container.bitrate_drop_percent}%`}
                   warn={report.container.warn_bitrate} />
            </Block>

            <Block title="Áudio" ok={report.audio?.ok}>
              <Row k="LUFS" v={`${report.audio.lufs} (alvo ${report.audio.target_lufs})`} />
              <Row k="pico real" v={`${report.audio.true_peak_db} dBTP`} />
              <Row k="crest factor" v={`${report.audio.crest_factor_db} dB`} />
              <Row k="amostras no teto" v={String(report.audio.samples_over_ceiling)}
                   warn={report.audio.samples_over_ceiling > 0} />
            </Block>

            <Block title="Palavra a palavra" ok={report.words?.ok}>
              {report.words?.available === false ? (
                <p className="hint">{report.words.reason}</p>
              ) : (
                <>
                  <Row k="esperadas" v={String(report.words.expected_count)} />
                  <Row k="ouvidas" v={String(report.words.actual_count)} />
                  <Row k="faltando" v={String(report.words.missing_count)}
                       warn={report.words.missing_count > 0} />
                  <Row k="truncadas" v={String(report.words.truncated_count)}
                       warn={report.words.truncated_count > 0} />
                  {report.words.truncated?.slice(0, 6).map((t: any, i: number) => (
                    <p key={i} className="text-[11px] text-red-300">
                      “{t.expected}” saiu como “{t.heard}”
                    </p>
                  ))}
                  {report.words.missing?.slice(0, 6).map((t: any, i: number) => (
                    <p key={i} className="text-[11px] text-red-300">
                      “{t.expected}” não aparece no resultado
                    </p>
                  ))}
                </>
              )}
            </Block>

            <Block title="Sincronia da legenda" ok={report.subtitles?.ok}>
              {report.subtitles?.available === false ? (
                <p className="hint">{report.subtitles.reason}</p>
              ) : (
                <>
                  <Row k="desvio médio"
                       v={`${report.subtitles.mean_abs_deviation} s (tolerância ${report.subtitles.tolerance} s)`}
                       warn={!report.subtitles.ok} />
                  <Row k="pior caso"
                       v={`${report.subtitles.max_abs_deviation} s em “${report.subtitles.worst?.word}”`} />
                  <Row k="sondas" v={String(report.subtitles.probes?.length ?? 0)} />
                </>
              )}
            </Block>
          </div>
        </section>
      )}

      <section className="card p-3">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">
          Salvar tudo como preset
        </h3>
        <div className="flex gap-1.5">
          <input className="field" placeholder="nome do preset" value={presetName}
                 onChange={(e) => setPresetName(e.target.value)} />
          <button className="btn btn-xs" disabled={!presetName}
                  onClick={async () => {
                    await api.savePreset({
                      name: presetName,
                      description: 'preset salvo do projeto ' + project.name,
                      cut: project.plan.cut, speed: project.plan.speed,
                      style: project.plan.style, audio: project.plan.audio,
                      export: project.plan.export,
                    })
                    setPresetName('')
                    toast('ok', 'Preset salvo')
                  }}>
            salvar
          </button>
        </div>
        <p className="hint mt-1.5">
          Guarda corte, velocidade, legenda, áudio e exportação para reaplicar em outro vídeo.
        </p>
      </section>
    </div>
  )
}

function Metric({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className={`font-mono text-sm ${
        ok === undefined ? '' : ok ? 'text-emerald-400' : 'text-amber-400'}`}>
        {value}
      </div>
    </div>
  )
}

function Block({ title, ok, children }: any) {
  return (
    <div className="border border-line rounded-md p-2.5">
      <div className="flex items-center gap-2 mb-1.5">
        <h4 className="text-xs font-medium text-slate-300">{title}</h4>
        {ok !== undefined && (
          <span className={`chip ml-auto ${ok
            ? 'border-emerald-800 text-emerald-300'
            : 'border-amber-800 text-amber-300'}`}>
            {ok ? 'ok' : 'atenção'}
          </span>
        )}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  )
}

function Row({ k, v, warn }: { k: string; v: string; warn?: boolean }) {
  return (
    <div className="flex justify-between text-[11px]">
      <span className="text-slate-500">{k}</span>
      <span className={`font-mono ${warn ? 'text-amber-400' : 'text-slate-300'}`}>{v}</span>
    </div>
  )
}
