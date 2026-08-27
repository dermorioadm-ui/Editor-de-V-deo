import { useEffect, useState } from 'react'
import FileBrowser from './FileBrowser'
import TonemapCompare from './TonemapCompare'
import { api } from '../lib/api'
import { timecode } from '../lib/format'
import { toast, useStore } from '../state/store'

interface Props {
  onChanged: () => Promise<any>
  snapshot: () => void
  safeZone: any
}

const VIDEO_EXT = ['.mp4', '.mov', '.mkv', '.m4v', '.avi', '.webm']
const IMAGE_EXT = ['.png', '.jpg', '.jpeg', '.webp', '.bmp']
const AUDIO_EXT = ['.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg']

export default function MediaPanel({ onChanged, snapshot, safeZone }: Props) {
  const project = useStore((s) => s.project)
  const view = useStore((s) => s.timeline)
  const playhead = useStore((s) => s.playhead)
  const [picking, setPicking] = useState<null | 'video' | 'image' | 'audio'>(null)
  const [busy, setBusy] = useState(false)

  if (!project || !view) return null
  const media = project.media ?? []

  const addMedia = async (path: string, kind: string) => {
    setBusy(true)
    try {
      await api.addMedia(project.id, path, kind)
      await onChanged()
      toast('ok', 'Mídia adicionada', path)
    } catch (e: any) {
      toast('error', 'Falha ao adicionar', String(e.message ?? e))
    } finally { setBusy(false); setPicking(null) }
  }

  const mainHdr = project.info?.is_hdr

  return (
    <div className="p-4 space-y-5 max-w-5xl">
      {/* --------------------------------------------------- biblioteca */}
      <section className="card p-3">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Mídia do projeto
          </h3>
          <div className="ml-auto flex gap-1.5">
            <button className="btn btn-xs" onClick={() => setPicking('video')}>+ vídeo</button>
            <button className="btn btn-xs" onClick={() => setPicking('image')}>+ foto/PNG</button>
            <button className="btn btn-xs" onClick={() => setPicking('audio')}>+ trilha</button>
          </div>
        </div>
        {!media.length && <p className="hint">Nada importado ainda.</p>}
        <div className="grid gap-2 sm:grid-cols-2">
          {media.map((m) => {
            const hdr = m.info?.is_hdr
            return (
              <div key={m.id} className="border border-line rounded-md p-2.5 text-xs">
                <div className="flex items-center gap-2">
                  <span className="chip border-line text-slate-400">{m.kind}</span>
                  <span className="font-medium truncate flex-1">{m.name}</span>
                </div>
                <p className="text-slate-500 font-mono text-[10px] truncate mt-1">
                  {m.path}
                </p>
                {m.info?.width > 0 && (
                  <p className="text-slate-500 mt-1">
                    {m.info.display_width}×{m.info.display_height}
                    {m.info.duration ? ` · ${timecode(m.info.duration)}` : ''}
                    {hdr && <span className="text-amber-400"> · HDR</span>}
                  </p>
                )}
                {hdr && !mainHdr && (
                  <p className="text-[10px] text-amber-400/90 mt-1 leading-snug">
                    HLG/BT.2020 detectado. O tonemap para BT.709 entra automaticamente —
                    sem ele o inserto entra mais claro e pisca na emenda.
                  </p>
                )}
                <div className="flex flex-wrap gap-1 mt-2">
                  {m.kind === 'video' && (
                    <>
                      <button className="btn btn-xs" disabled={busy}
                              onClick={async () => {
                                snapshot()
                                await api.addCutaway(project.id, {
                                  media_id: m.id,
                                  out_start: playhead,
                                  out_end: Math.min(view.duration,
                                    playhead + Math.min(6, m.info?.duration ?? 5)),
                                  media_start: 0,
                                })
                                await onChanged()
                                toast('ok', 'Cutaway criado',
                                  'O vídeo entra por cima e o áudio original continua por baixo.')
                              }}>
                        substituir (cutaway) aqui
                      </button>
                      <button className="btn btn-xs" disabled={busy}
                              onClick={async () => {
                                snapshot()
                                await api.insert(project.id, {
                                  media_id: m.id, kind: 'insert', at: playhead,
                                  media_start: 0, media_end: m.info?.duration ?? 5,
                                })
                                await onChanged()
                                toast('ok', 'Inserido com o próprio áudio')
                              }}>
                        inserir empurrando
                      </button>
                    </>
                  )}
                  {m.kind === 'image' && (
                    <>
                      <button className="btn btn-xs" disabled={busy}
                              onClick={async () => {
                                snapshot()
                                await api.insert(project.id, {
                                  media_id: m.id, kind: 'photo', at: playhead,
                                  duration: 3.0,
                                  ken_burns: { enabled: true, intensity: 0.12, direction: 'in' },
                                })
                                await onChanged()
                                toast('ok', 'Foto inserida com push-in')
                              }}>
                        inserir foto (3 s, push-in)
                      </button>
                      <button className="btn btn-xs" disabled={busy}
                              onClick={async () => {
                                snapshot()
                                await api.addOverlay(project.id, {
                                  media_id: m.id, out_start: playhead,
                                  out_end: playhead + 3,
                                  x: safeZone?.anchor?.x ?? 0.5,
                                  y: safeZone?.anchor?.y ?? 0.15,
                                })
                                await onChanged()
                                toast('ok', 'Sobreposição criada na âncora do topo')
                              }}>
                        sobrepor PNG
                      </button>
                    </>
                  )}
                  {m.kind === 'audio' && (
                    <button className="btn btn-xs" disabled={busy}
                            onClick={async () => {
                              snapshot()
                              await api.setMusic(project.id, {
                                media_id: m.id, gain_db: -18, ducking: true,
                                duck_amount: 12, fade_in: 1, fade_out: 2, enabled: true,
                              })
                              await onChanged()
                              toast('ok', 'Trilha ligada com ducking por sidechain')
                            }}>
                      usar como trilha
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </section>

      {/* ----------------------------------------------------- cutaways */}
      <section className="card p-3">
        <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-2">
          Cutaways · {view.cutaways.length}
        </h3>
        <p className="hint mb-2">
          O vídeo entra e o áudio original continua por baixo. Proporção diferente encaixa
          sobre um fundo desfocado dele mesmo — nada de tarja preta, nada de cortar conteúdo.
        </p>
        {view.cutaways.map((c: any) => (
          <div key={c.id} className="border-t border-line pt-2 mt-2 first:border-0
                                     first:pt-0 first:mt-0 text-xs space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="font-mono text-slate-400">
                {timecode(c.out_start, true)} → {timecode(c.out_end, true)}
              </span>
              <span className="text-slate-500 truncate flex-1">
                {media.find((m) => m.id === c.media_id)?.name}
              </span>
              <button className="btn btn-xs btn-danger"
                      onClick={async () => {
                        snapshot()
                        await api.deleteCutaway(project.id, c.id)
                        await onChanged()
                      }}>remover</button>
            </div>
            <div className="grid grid-cols-4 gap-2">
              <label className="block">
                <span className="label">início</span>
                <input className="field" type="number" step={0.05} defaultValue={c.out_start}
                       onBlur={async (e) => {
                         await api.updateCutaway(project.id, c.id,
                           { out_start: +e.target.value }); await onChanged()
                       }} />
              </label>
              <label className="block">
                <span className="label">fim</span>
                <input className="field" type="number" step={0.05} defaultValue={c.out_end}
                       onBlur={async (e) => {
                         await api.updateCutaway(project.id, c.id,
                           { out_end: +e.target.value }); await onChanged()
                       }} />
              </label>
              <label className="block">
                <span className="label">entra em</span>
                <input className="field" type="number" step={0.05} defaultValue={c.media_start}
                       onBlur={async (e) => {
                         await api.updateCutaway(project.id, c.id,
                           { media_start: +e.target.value }); await onChanged()
                       }} />
              </label>
              <label className="block">
                <span className="label">velocidade</span>
                <input className="field" type="number" step={0.01} defaultValue={c.speed}
                       onBlur={async (e) => {
                         await api.updateCutaway(project.id, c.id,
                           { speed: +e.target.value }); await onChanged()
                       }} />
              </label>
            </div>
            <p className="hint">
              Para casar {(c.out_end - c.out_start).toFixed(1)} s de narração com o inserto,
              a velocidade precisa ser{' '}
              <b className="font-mono">
                {((media.find((m) => m.id === c.media_id)?.info?.duration ?? 0) /
                  Math.max(c.out_end - c.out_start, 0.01)).toFixed(2)}x
              </b>{' '}
              se você quiser usar o inserto inteiro.
            </p>
            <div className="grid grid-cols-4 gap-2">
              <label className="block">
                <span className="label">tonemap</span>
                <select className="field" defaultValue={String(c.fit?.tonemap ?? 'auto')}
                        onChange={async (e) => {
                          const v = e.target.value
                          await api.updateCutaway(project.id, c.id, {
                            fit: { tonemap: v === 'auto' ? 'auto' : v === 'sim' },
                          })
                          await onChanged()
                        }}>
                  <option value="auto">automático (só se a fonte for HDR)</option>
                  <option value="sim">forçar</option>
                  <option value="nao">desligar</option>
                </select>
              </label>
              <label className="block">
                <span className="label">modo</span>
                <select className="field"
                        defaultValue={String(c.fit?.tonemap_mode ?? 'transferencia')}
                        onChange={async (e) => {
                          await api.updateCutaway(project.id, c.id,
                            { fit: { tonemap_mode: e.target.value } })
                          await onChanged()
                        }}>
                  <option value="transferencia">só transferência (padrão)</option>
                  <option value="operador">com operador de tonemap</option>
                </select>
              </label>
              {(['brightness', 'saturation', 'contrast'] as const).map((k) => (
                <label className="block" key={k}>
                  <span className="label">
                    {k === 'brightness' ? 'brilho' : k === 'saturation' ? 'saturação' : 'contraste'}
                  </span>
                  <input className="field" type="number" step={0.05}
                         defaultValue={c.fit?.[k] ?? (k === 'brightness' ? 0 : 1)}
                         onBlur={async (e) => {
                           await api.updateCutaway(project.id, c.id,
                             { fit: { [k]: +e.target.value } })
                           await onChanged()
                         }} />
                </label>
              ))}
            </div>
            {media.find((m) => m.id === c.media_id)?.info?.is_hdr && (
              <TonemapCompare projectId={project.id} mediaId={c.media_id}
                              fit={c.fit} time={c.media_start + 0.5}
                              mainTime={c.out_start}
                              onApply={async (fit) => {
                                await api.updateCutaway(project.id, c.id, { fit })
                                await onChanged()
                                toast('ok', 'Conversão aplicada a este cutaway')
                              }} />
            )}
          </div>
        ))}
        {!view.cutaways.length && <p className="hint">Nenhum cutaway.</p>}
      </section>

      {/* -------------------------------------------------- sobreposições */}
      <section className="card p-3">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Sobreposições · {view.overlays.length}
          </h3>
          {safeZone?.band?.found && (
            <span className="chip border-amber-800 text-amber-300 ml-auto">
              faixa de legenda bloqueada: {(safeZone.band.top * 100).toFixed(0)}%–
              {(safeZone.band.bottom * 100).toFixed(0)}%
            </span>
          )}
        </div>
        <p className="hint mb-2">
          {safeZone?.anchor
            ? `Âncora sugerida: x ${(safeZone.anchor.x * 100).toFixed(0)}%, ` +
              `y ${(safeZone.anchor.y * 100).toFixed(0)}% — ${safeZone.anchor.reason}. ` +
              `Reaproveite a mesma para todos: consistência vale mais que variedade.`
            : 'Posicione tudo na mesma âncora do topo.'}
        </p>
        {view.overlays.map((o: any) => {
          const inBand = safeZone?.band?.found &&
            o.y >= safeZone.band.top && o.y <= safeZone.band.bottom
          return (
            <div key={o.id} className="border-t border-line pt-2 mt-2 first:border-0
                                       first:pt-0 first:mt-0 text-xs">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="truncate flex-1">
                  {media.find((m) => m.id === o.media_id)?.name}
                </span>
                {inBand && (
                  <span className="chip border-red-800 text-red-300">
                    em cima da legenda queimada
                  </span>
                )}
                <button className="btn btn-xs btn-danger"
                        onClick={async () => {
                          snapshot()
                          await api.deleteOverlay(project.id, o.id)
                          await onChanged()
                        }}>remover</button>
              </div>
              <div className="grid grid-cols-4 gap-2">
                {([['out_start', 'início'], ['out_end', 'fim'], ['x', 'x (0–1)'],
                   ['y', 'y (0–1)'], ['scale', 'escala'], ['opacity', 'opacidade'],
                   ['dur_in', 'entrada (s)'], ['dur_out', 'saída (s)']] as const).map(
                  ([key, label]) => (
                    <label className="block" key={key}>
                      <span className="label">{label}</span>
                      <input className="field" type="number" step={0.05}
                             defaultValue={o[key]}
                             onBlur={async (e) => {
                               const value = +e.target.value
                               if (key === 'y' && safeZone?.band?.found &&
                                   value >= safeZone.band.top && value <= safeZone.band.bottom) {
                                 toast('warn', 'Zona segura',
                                   'Essa altura cai em cima da legenda queimada. ' +
                                   'Escolha outra ou use a âncora do topo.')
                               }
                               await api.updateOverlay(project.id, o.id, { [key]: value })
                               await onChanged()
                             }} />
                    </label>
                  ))}
                <label className="block">
                  <span className="label">entrada</span>
                  <select className="field" defaultValue={o.anim_in}
                          onChange={async (e) => {
                            await api.updateOverlay(project.id, o.id,
                              { anim_in: e.target.value }); await onChanged()
                          }}>
                    <option value="fade">fade</option>
                    <option value="slide_left">deslizar da esquerda</option>
                    <option value="slide_right">deslizar da direita</option>
                    <option value="pop">pop (sobe 26 px)</option>
                    <option value="none">nenhuma</option>
                  </select>
                </label>
              </div>
            </div>
          )
        })}
        {!view.overlays.length && <p className="hint">Nenhuma sobreposição.</p>}
      </section>

      {/* ------------------------------------------------------- desfoque */}
      <section className="card p-3">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Desfoque de área · {view.blurs.length}
          </h3>
          <button className="btn btn-xs ml-auto"
                  onClick={async () => {
                    snapshot()
                    await api.addBlur(project.id, {
                      out_start: playhead, out_end: playhead + 3, strength: 24,
                      keyframes: [{ t: playhead, x: 0.35, y: 0.35, w: 0.3, h: 0.3 }],
                    })
                    await onChanged()
                  }}>
            + região no playhead
          </button>
        </div>
        <p className="hint mb-2">
          Para proteger rosto de terceiro e documento. Adicione keyframes para a região
          acompanhar o movimento.
        </p>
        {view.blurs.map((b: any) => (
          <div key={b.id} className="border-t border-line pt-2 mt-2 first:border-0
                                     first:pt-0 first:mt-0 text-xs space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="font-mono text-slate-400">
                {timecode(b.out_start, true)} → {timecode(b.out_end, true)}
              </span>
              <span className="text-slate-500">{b.keyframes.length} keyframe(s)</span>
              <button className="btn btn-xs ml-auto"
                      onClick={async () => {
                        const last = b.keyframes[b.keyframes.length - 1] ?? {}
                        const kfs = [...b.keyframes,
                          { t: playhead, x: last.x ?? 0.35, y: last.y ?? 0.35,
                            w: last.w ?? 0.3, h: last.h ?? 0.3 }]
                          .sort((p: any, q: any) => p.t - q.t)
                        await api.updateBlur(project.id, b.id, { keyframes: kfs })
                        await onChanged()
                      }}>+ keyframe aqui</button>
              <button className="btn btn-xs btn-danger"
                      onClick={async () => {
                        snapshot()
                        await api.deleteBlur(project.id, b.id)
                        await onChanged()
                      }}>remover</button>
            </div>
            <div className="grid grid-cols-4 gap-2">
              <label className="block">
                <span className="label">início</span>
                <input className="field" type="number" step={0.05} defaultValue={b.out_start}
                       onBlur={async (e) => {
                         await api.updateBlur(project.id, b.id, { out_start: +e.target.value })
                         await onChanged()
                       }} />
              </label>
              <label className="block">
                <span className="label">fim</span>
                <input className="field" type="number" step={0.05} defaultValue={b.out_end}
                       onBlur={async (e) => {
                         await api.updateBlur(project.id, b.id, { out_end: +e.target.value })
                         await onChanged()
                       }} />
              </label>
              <label className="block">
                <span className="label">intensidade</span>
                <input className="field" type="number" defaultValue={b.strength}
                       onBlur={async (e) => {
                         await api.updateBlur(project.id, b.id, { strength: +e.target.value })
                         await onChanged()
                       }} />
              </label>
              <label className="block">
                <span className="label">modo</span>
                <select className="field" defaultValue={b.shape ?? 'blur'}
                        onChange={async (e) => {
                          await api.updateBlur(project.id, b.id, { shape: e.target.value })
                          await onChanged()
                        }}>
                  <option value="blur">desfoque gaussiano</option>
                  <option value="pixel">mosaico (mais seguro)</option>
                </select>
              </label>
            </div>
            {b.keyframes.map((k: any, i: number) => (
              <div key={i} className="grid grid-cols-5 gap-1.5 items-end">
                <span className="text-[10px] text-slate-500 font-mono pb-1.5">
                  t={k.t.toFixed(2)}
                </span>
                {(['x', 'y', 'w', 'h'] as const).map((axis) => (
                  <input key={axis} className="field" type="number" step={0.01}
                         defaultValue={k[axis]}
                         onBlur={async (e) => {
                           const kfs = b.keyframes.map((kk: any, j: number) =>
                             j === i ? { ...kk, [axis]: +e.target.value } : kk)
                           await api.updateBlur(project.id, b.id, { keyframes: kfs })
                           await onChanged()
                         }} />
                ))}
              </div>
            ))}
          </div>
        ))}
      </section>

      {picking && (
        <FileBrowser
          title={picking === 'video' ? 'Escolher vídeo'
            : picking === 'image' ? 'Escolher imagem' : 'Escolher trilha'}
          extensions={picking === 'video' ? VIDEO_EXT
            : picking === 'image' ? IMAGE_EXT : AUDIO_EXT}
          onClose={() => setPicking(null)}
          onPick={(p) => addMedia(p, picking)} />
      )}
    </div>
  )
}
