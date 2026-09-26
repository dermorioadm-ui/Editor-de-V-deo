import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { toast, useStore } from '../state/store'

/**
 * O B-ROLL CLICADO NO TRILHO. Tudo o que se faz com ele, num lugar:
 * onde começa no vídeo e quanto dura (digitando), QUE PEDAÇO do vídeo de
 * b-roll aparece (um vídeo de 1 min mostrando só os 15 s escolhidos, com a
 * prévia tocando o trecho), a velocidade, e SUBSTITUIR por outro — da
 * biblioteca, do banco grátis ou do computador — sem perder o lugar.
 */
export default function BrollInspector({ cutawayId, onChanged, snapshot, onClose }: {
  cutawayId: string
  onChanged: () => Promise<any>; snapshot: () => void; onClose: () => void
}) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const cut = (view?.cutaways ?? []).find((c: any) => c.id === cutawayId)
  const media = (project?.media ?? []).find((m: any) => m.id === cut?.media_id)
  const durMidia = Number(media?.info?.duration ?? 0)

  const [inicio, setInicio] = useState(0)
  const [dura, setDura] = useState(0)
  const [entra, setEntra] = useState(0)
  const [vel, setVel] = useState(1)
  const [salvando, setSalvando] = useState(false)
  const [trocar, setTrocar] = useState<null | 'biblioteca' | 'banco'>(null)
  const [biblioteca, setBiblioteca] = useState<any[]>([])
  const [termo, setTermo] = useState('')
  const [achados, setAchados] = useState<any[]>([])
  const [buscando, setBuscando] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useStore((s) => (jobId ? s.jobs[jobId] : undefined))
  const video = useRef<HTMLVideoElement>(null)

  // os campos seguem o b-roll: clicar em outro, arrastar no trilho ou
  // trocar o vídeo recarrega os números
  useEffect(() => {
    if (!cut) return
    setInicio(cut.out_start); setDura(+(cut.out_end - cut.out_start).toFixed(2))
    setEntra(cut.media_start ?? 0); setVel(cut.speed ?? 1)
    setTermo(cut.termo || '')
  }, [cut?.id, cut?.out_start, cut?.out_end, cut?.media_start, cut?.speed, cut?.media_id])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!job || !jobId) return
    if (job.status === 'ok') {
      setJobId(null); setTrocar(null)
      onChanged().then(() => toast('ok', 'B-roll trocado',
        'O vídeo novo entrou no mesmo lugar. Ajuste o trecho aqui embaixo.'))
    } else if (job.status === 'erro' || job.status === 'cancelado') {
      setJobId(null)
      toast('error', 'Não deu para trocar', job.error || job.message)
    }
  }, [job?.status])   // eslint-disable-line react-hooks/exhaustive-deps

  // a prévia toca SÓ o trecho escolhido, em laço
  const usa = dura * vel
  useEffect(() => {
    const v = video.current
    if (!v) return
    const tick = () => {
      if (v.currentTime < entra - 0.05 || v.currentTime > entra + usa) v.currentTime = entra
    }
    v.addEventListener('timeupdate', tick)
    return () => v.removeEventListener('timeupdate', tick)
  }, [entra, usa])
  useEffect(() => {
    const v = video.current
    if (v && Math.abs(v.currentTime - entra) > 0.1) v.currentTime = entra
  }, [entra])

  const maxEntra = useMemo(() => Math.max(0, durMidia - usa), [durMidia, usa])
  if (!project || !cut) return null
  const auto = cut.origem === 'auto'

  const salvar = async (extra: Record<string, number> = {}) => {
    snapshot()
    setSalvando(true)
    try {
      const d = Math.max(0.2, dura)
      const r = await api.updateCutaway(project.id, cut.id, {
        out_start: Math.max(0, inicio), out_end: Math.max(0, inicio) + d,
        media_start: Math.max(0, entra), speed: vel, ...extra,
      })
      await onChanged()
      const c = r.cutaway ?? {}
      const real = (c.out_end ?? 0) - (c.out_start ?? 0)
      if (Math.abs(real - d) > 0.05) {
        toast('info', `Ficou com ${real.toFixed(1)} s`,
          'O vídeo do b-roll acaba antes: a janela encolhe para ele nunca faltar.')
      } else {
        toast('ok', 'B-roll ajustado',
          `${timecode(c.out_start)} → ${timecode(c.out_end)}, mostrando de `
          + `${(c.media_start ?? 0).toFixed(1)} s do vídeo dele`)
      }
    } catch (e: any) {
      toast('warn', 'Não deu para ajustar', String(e.message ?? e))
    } finally { setSalvando(false) }
  }

  const trocarPor = async (path: string) => {
    snapshot()
    try {
      const existente = (project.media ?? []).find((m: any) => m.path === path)
      const mid = existente?.id ?? (await api.addMedia(project.id, path, 'video'))?.id
      await api.updateCutaway(project.id, cut.id, { media_id: mid, media_start: 0 })
      setTrocar(null)
      await onChanged()
      toast('ok', 'B-roll trocado', 'O vídeo novo entrou no mesmo lugar.')
    } catch (e: any) {
      toast('warn', 'Não deu para trocar', String(e.message ?? e))
    }
  }

  const doComputador = async () => {
    try {
      const r = await api.escolher('video', 'Escolher o vídeo do b-roll')
      if (!r.cancelado) await trocarPor(r.path)
    } catch (e: any) {
      toast('warn', 'Esta máquina não abriu a janela do sistema', String(e.message ?? e))
    }
  }

  const buscar = async () => {
    if (!termo.trim()) return
    setBuscando(true)
    try {
      const r = await api.bancoBuscar(termo.trim(), '', project.id, 1)
      setAchados(r.itens ?? [])
      if (!r.itens?.length) toast('info', 'Nada achado', 'Tente outra palavra.')
    } catch (e: any) {
      toast('warn', 'A busca não deu', String(e.message ?? e))
    } finally { setBuscando(false) }
  }

  const ocupado = !!jobId && !!job && ['fila', 'rodando'].includes(job.status)

  return (
    <section className="card p-3 m-3 space-y-2.5 border-sky-700/60" data-broll-inspector="1">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-xs font-semibold text-sky-300 uppercase tracking-wide">B-roll</h3>
          <p className="text-[11px] text-slate-300 truncate" title={media?.name}>{media?.name ?? 'vídeo'}</p>
          {auto && (
            <p className="text-[10px] text-amber-300">
              automático{cut.termo ? ` · buscou “${cut.termo}”` : ''}</p>
          )}
        </div>
        <button className="btn btn-xs" onClick={onClose} title="fechar">×</button>
      </div>

      {media && (
        <video ref={video} key={media.id} src={api.mediaFileUrl(project.id, media.id)}
               className="w-full max-h-44 rounded bg-black object-contain"
               muted playsInline controls
               onLoadedMetadata={(e) => { e.currentTarget.currentTime = entra }} />
      )}

      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="label">começa em (s)</span>
          <input className="field w-full text-xs py-1" type="number" step={0.1} min={0} value={inicio}
                 data-campo="inicio"
                 onChange={(e) => setInicio(+e.target.value)} />
        </label>
        <label className="block">
          <span className="label">dura (s)</span>
          <input className="field w-full text-xs py-1" type="number" step={0.1} min={0.2} value={dura}
                 data-campo="dura"
                 onChange={(e) => setDura(+e.target.value)} />
        </label>
      </div>
      <p className="text-[10px] text-slate-500 -mt-1">
        no vídeo: {timecode(inicio)} → {timecode(inicio + dura)}
      </p>

      <div>
        <span className="label">trecho do b-roll que aparece</span>
        <input type="range" min={0} max={Math.max(0.01, maxEntra)} step={0.05}
               value={Math.min(entra, maxEntra)} className="w-full" data-campo="trecho"
               disabled={maxEntra <= 0.01}
               onChange={(e) => setEntra(+e.target.value)} />
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-[11px] text-slate-400">
            de
            <input className="field w-16 text-xs py-0.5" type="number" step={0.1} min={0}
                   value={+entra.toFixed(2)} data-campo="entra"
                   onChange={(e) => setEntra(Math.max(0, +e.target.value))} />
            s
          </label>
          <span className="text-[11px] text-slate-400">
            até {(entra + usa).toFixed(1)} s{durMidia ? ` de um vídeo de ${durMidia.toFixed(1)} s` : ''}
          </span>
        </div>
        {durMidia > 0 && entra + usa > durMidia + 0.05 && (
          <p className="text-[10px] text-amber-300">
            passa do fim do vídeo: ao aplicar, a duração encolhe para caber</p>
        )}
      </div>

      <label className="flex items-center gap-2 text-[11px] text-slate-400">
        velocidade
        <input className="field w-16 text-xs py-0.5" type="number" step={0.05} min={0.25} max={4}
               value={vel} onChange={(e) => setVel(+e.target.value || 1)} />
        x
      </label>

      <div className="flex flex-wrap gap-1.5">
        <button className="btn btn-xs btn-primary" disabled={salvando} data-aplicar="1"
                onClick={() => salvar()}>{salvando ? 'aplicando…' : 'aplicar'}</button>
        <button className={`btn btn-xs ${trocar ? 'border-sky-500' : ''}`}
                onClick={() => {
                  const prox = trocar ? null : 'biblioteca'
                  setTrocar(prox)
                  if (prox) api.bancoBaixados().then(setBiblioteca).catch(() => setBiblioteca([]))
                }}>substituir…</button>
        <button className="btn btn-xs btn-danger ml-auto"
                onClick={async () => {
                  snapshot()
                  await api.deleteCutaway(project.id, cut.id)
                  await onChanged()
                  onClose()
                  toast('ok', 'B-roll apagado', 'A imagem da fala volta nesse trecho.')
                }}>apagar</button>
      </div>

      {trocar && (
        <div className="border-t border-line pt-2 space-y-2" data-substituir="1">
          <div className="flex gap-1">
            <button className={`btn btn-xs ${trocar === 'biblioteca' ? 'btn-primary' : ''}`}
                    onClick={() => setTrocar('biblioteca')}>biblioteca</button>
            <button className={`btn btn-xs ${trocar === 'banco' ? 'btn-primary' : ''}`}
                    onClick={() => setTrocar('banco')}>banco grátis</button>
            <button className="btn btn-xs" onClick={doComputador}>do computador…</button>
          </div>
          {trocar === 'biblioteca' && (
            biblioteca.length ? (
              <div className="grid grid-cols-3 gap-1.5 max-h-56 overflow-auto">
                {biblioteca.map((b) => (
                  <button key={b.id} disabled={b.path === media?.path}
                          className={`rounded overflow-hidden border text-left ${
                            b.path === media?.path ? 'border-sky-500 opacity-60' : 'border-line'}`}
                          title={b.path === media?.path ? 'é o que está aí agora'
                            : `${b.termo || b.nome || ''} — de ${b.autor}`}
                          data-em-uso={b.path === media?.path ? '1' : undefined}
                          onClick={() => trocarPor(b.path)}>
                    {b.miniatura
                      ? <img src={b.miniatura} alt="" className="w-full h-14 object-cover"
                             onError={(e) => { e.currentTarget.style.visibility = 'hidden' }} />
                      : <div className="h-14 bg-ink-900" />}
                    <span className="block px-1 text-[9px] text-slate-400 truncate">
                      {b.termo || b.nome || b.arquivo}</span>
                  </button>
                ))}
              </div>
            ) : <p className="hint">A biblioteca está vazia. Busque no banco grátis ou envie os seus na aba Mídia.</p>
          )}
          {trocar === 'banco' && (
            <>
              <div className="flex gap-1">
                <input className="field flex-1 text-xs py-1" value={termo} placeholder="o que mostrar?"
                       onChange={(e) => setTermo(e.target.value)}
                       onKeyDown={(e) => { if (e.key === 'Enter') buscar() }} />
                <button className="btn btn-xs btn-primary" disabled={buscando} onClick={buscar}>
                  {buscando ? '…' : 'buscar'}</button>
              </div>
              {ocupado && <p className="text-[11px] text-sky-300">{job?.message || 'baixando…'}</p>}
              <div className="grid grid-cols-3 gap-1.5 max-h-56 overflow-auto">
                {achados.map((it) => (
                  <button key={it.id} className="rounded overflow-hidden border border-line text-left"
                          disabled={ocupado} title={`de ${it.autor}`}
                          onClick={async () => {
                            snapshot()
                            try {
                              const j = await api.bancoSubstituir(project.id, it.id, cut.id, termo)
                              setJobId(j.id)
                            } catch (e: any) {
                              toast('warn', 'Não deu para baixar', String(e.message ?? e))
                            }
                          }}>
                    {it.miniatura
                      ? <img src={it.miniatura} alt="" className="w-full h-14 object-cover" />
                      : <div className="h-14 bg-ink-900" />}
                    <span className="block px-1 text-[9px] text-slate-400 truncate">
                      {Math.round(it.duracao)} s · {it.autor}</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  )
}
