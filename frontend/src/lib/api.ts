import type { Envelope, Job, Project, TimelineView } from '../types'

async function req<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch { /* corpo não-JSON */ }
    throw new Error(detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const post = <T>(url: string, body?: unknown) =>
  req<T>(url, { method: 'POST', body: JSON.stringify(body ?? {}) })
const put = <T>(url: string, body?: unknown) =>
  req<T>(url, { method: 'PUT', body: JSON.stringify(body ?? {}) })
const del = <T>(url: string) => req<T>(url, { method: 'DELETE' })

export const api = {
  health: () => req<any>('/api/health'),
  browse: (path: string) => req<any>(`/api/browse?path=${encodeURIComponent(path)}`),
  locate: (name: string, size: number) =>
    post<any>('/api/locate', { name, size }),

  projects: () => req<any[]>('/api/projects'),
  project: (id: string) => req<Project>(`/api/projects/${id}`),
  createProject: (source_path: string, name: string, preset: string) =>
    post<Project>('/api/projects', { source_path, name, preset }),
  deleteProject: (id: string) => del<any>(`/api/projects/${id}`),

  envelope: (id: string, points = 4000) =>
    req<Envelope>(`/api/projects/${id}/envelope?points=${points}`),

  analyze: (id: string) => post<Job>(`/api/projects/${id}/analyze`),
  autoedit: (id: string) => post<Job>(`/api/projects/${id}/autoedit`),
  oneclick: (id: string, preset?: string) =>
    post<Job>(`/api/projects/${id}/oneclick`, { preset }),
  exportProject: (id: string, options: any) =>
    post<Job>(`/api/projects/${id}/export`, options),
  preview: (id: string, scale = '480') =>
    post<Job>(`/api/projects/${id}/preview`, { scale }),
  calibrateDeesser: (id: string, payload: any) =>
    post<any>(`/api/projects/${id}/audio/calibrate-deesser`, payload),
  validate: (id: string, output?: string) =>
    post<Job>(`/api/projects/${id}/validate`, { output }),
  jobs: (project?: string) =>
    req<Job[]>(`/api/jobs${project ? `?project_id=${project}` : ''}`),
  cancelJob: (jobId: string) => post<any>(`/api/jobs/${jobId}/cancel`),

  params: (id: string, payload: any) =>
    post<any>(`/api/projects/${id}/params`, payload),
  applyPreset: (id: string, name: string) =>
    post<any>(`/api/projects/${id}/preset`, { name }),
  presets: () => req<any[]>('/api/presets'),
  savePreset: (data: any) => post<any>('/api/presets', data),
  deletePreset: (name: string) => del<any>(`/api/presets/${encodeURIComponent(name)}`),

  replacePlan: (id: string, entry: { plan: any; removedWordIds?: number[];
                                     manualRemovedWordIds?: number[] }) =>
    post<{ timeline: TimelineView }>(`/api/projects/${id}/plan`, {
      plan: entry.plan,
      removed_word_ids: entry.removedWordIds,
      manual_removed_word_ids: entry.manualRemovedWordIds,
    }),

  deleteRange: (id: string, start: number, end: number) =>
    post<any>(`/api/projects/${id}/ops/delete-range`, { start, end }),
  removeWords: (id: string, word_ids: number[]) =>
    post<any>(`/api/projects/${id}/ops/remove-words`, { word_ids }),
  restoreWords: (id: string, word_ids: number[]) =>
    post<any>(`/api/projects/${id}/ops/restore-words`, { word_ids }),
  restoreRange: (id: string, start: number, end: number) =>
    post<any>(`/api/projects/${id}/ops/restore-range`, { start, end }),
  splitClip: (id: string, clip_id: string, time: number) =>
    post<any>(`/api/projects/${id}/ops/split`, { clip_id, time }),
  mergeClips: (id: string, clip_ids: string[]) =>
    post<any>(`/api/projects/${id}/ops/merge`, { clip_ids }),
  setSpeed: (id: string, clip_id: string, speed: number) =>
    post<any>(`/api/projects/${id}/ops/speed`, { clip_id, speed }),
  setGlobalSpeed: (id: string, value: number) =>
    post<any>(`/api/projects/${id}/ops/speed`, { global: value }),
  setSection: (id: string, clip_id: string, section: string) =>
    post<any>(`/api/projects/${id}/ops/section`, { clip_id, section }),
  fixAudit: (id: string, index: number) =>
    post<any>(`/api/projects/${id}/ops/audit-fix`, { index }),
  setTake: (id: string, take_id: string, restored: boolean) =>
    post<any>(`/api/projects/${id}/ops/take`, { take_id, restored }),
  resizeRemoved: (id: string, start: number, end: number,
                  new_start: number, new_end: number) =>
    post<any>(`/api/projects/${id}/ops/resize-removed`,
              { start, end, new_start, new_end }),

  moveItem: (id: string, kind: string, item: string, delta: number) =>
    post<any>(`/api/projects/${id}/ops/item`,
              { kind, id: item, action: 'move', delta }),
  resizeItem: (id: string, kind: string, item: string,
               side: 'start' | 'end', time: number) =>
    post<any>(`/api/projects/${id}/ops/item`,
              { kind, id: item, action: 'resize', side, time }),
  deleteItem: (id: string, kind: string, item: string) =>
    post<any>(`/api/projects/${id}/ops/item`, { kind, id: item, action: 'delete' }),
  lockZoom: (id: string, clip_id: string, locked: boolean) =>
    post<any>(`/api/projects/${id}/ops/zoom`, { clip_id, locked }),

  outputDir: () => req<{ path: string; default: string }>('/api/output-dir'),
  setOutputDir: (path: string) => post<any>('/api/output-dir', { path }),
  reveal: (path: string) => post<any>('/api/reveal', { path }),

  looks: () => req<any[]>('/api/looks'),
  setLook: (id: string, look: string, vignette?: number | null) =>
    post(`/api/projects/${id}/ops/look`,
         vignette === undefined ? { look } : { look, vignette }),

  setRepeat: (id: string, repeat_id: string, restored: boolean) =>
    post(`/api/projects/${id}/ops/repeat`, { repeat_id, restored }),

  setZoom: (id: string, clip_id: string, zoom: number) =>
    post(`/api/projects/${id}/ops/zoom`, { clip_id, zoom }),

  setClap: (id: string, clap_id: string, enabled: boolean) =>
    post<any>(`/api/projects/${id}/ops/clap`, { clap_id, enabled }),
  fillers: (id: string) => req<any[]>(`/api/projects/${id}/fillers`),

  rebuildSubtitles: (id: string) =>
    post<any>(`/api/projects/${id}/subtitles/rebuild`),
  editSubtitle: (id: string, sid: string, payload: any) =>
    put<any>(`/api/projects/${id}/subtitles/${sid}`, payload),
  calibrate: (id: string, target_px: number, sample: string, apply = true) =>
    post<any>(`/api/projects/${id}/style/calibrate`, { target_px, sample, apply }),

  corrections: () => req<any[]>('/api/corrections'),
  addCorrection: (from: string, to: string) =>
    post<any>('/api/corrections', { from, to }),
  updateCorrection: (id: number, from: string, to: string, enabled: boolean) =>
    put<any>(`/api/corrections/${id}`, { from, to, enabled }),
  deleteCorrection: (id: number) => del<any>(`/api/corrections/${id}`),

  addMedia: (id: string, path: string, kind: string) =>
    post<any>(`/api/projects/${id}/media`, { path, kind }),
  addCutaway: (id: string, payload: any) =>
    post<any>(`/api/projects/${id}/cutaways`, payload),
  updateCutaway: (id: string, cid: string, payload: any) =>
    put<any>(`/api/projects/${id}/cutaways/${cid}`, payload),
  deleteCutaway: (id: string, cid: string) =>
    del<any>(`/api/projects/${id}/cutaways/${cid}`),
  insert: (id: string, payload: any) => post<any>(`/api/projects/${id}/insert`, payload),
  addOverlay: (id: string, payload: any) =>
    post<any>(`/api/projects/${id}/overlays`, payload),
  updateOverlay: (id: string, oid: string, payload: any) =>
    put<any>(`/api/projects/${id}/overlays/${oid}`, payload),
  deleteOverlay: (id: string, oid: string) =>
    del<any>(`/api/projects/${id}/overlays/${oid}`),
  addBlur: (id: string, payload: any) => post<any>(`/api/projects/${id}/blurs`, payload),
  updateBlur: (id: string, bid: string, payload: any) =>
    put<any>(`/api/projects/${id}/blurs/${bid}`, payload),
  deleteBlur: (id: string, bid: string) => del<any>(`/api/projects/${id}/blurs/${bid}`),
  setMusic: (id: string, payload: any) => post<any>(`/api/projects/${id}/music`, payload),

  frameUrl: (id: string, t: number, source = 'main', width = 360, look = '') =>
    `/api/projects/${id}/frame?t=${t.toFixed(3)}&source=${source}&width=${width}`
    + (look ? `&look=${look}` : ''),
  updatePhoto: (id: string, cid: string, payload: any) =>
    put<any>(`/api/projects/${id}/clips/${cid}/photo`, payload),

  tonemapPreview: (id: string, mid: string, params: Record<string, string | number>) =>
    req<any>(`/api/projects/${id}/media/${mid}/tonemap-preview?` +
      new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))),

  audioAnalysis: (id: string) => req<any>(`/api/projects/${id}/audio/analysis`),
  audioPreview: (id: string, payload: any) =>
    post<any>(`/api/projects/${id}/audio/preview`, payload),
  safeZone: (id: string) => req<any>(`/api/projects/${id}/safe-zone`),
  bitrateEstimate: (id: string) => req<any>(`/api/projects/${id}/bitrate-estimate`),
}

export function connectJobs(onJob: (job: Job) => void): () => void {
  let socket: WebSocket | null = null
  let closed = false
  let retry: number | undefined

  const open = () => {
    if (closed) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    socket = new WebSocket(`${proto}://${location.host}/ws`)
    socket.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'job') onJob(msg.job as Job)
      } catch { /* ignora mensagem inválida */ }
    }
    socket.onclose = () => {
      if (!closed) retry = window.setTimeout(open, 1200)
    }
    socket.onerror = () => socket?.close()
  }
  open()
  return () => {
    closed = true
    window.clearTimeout(retry)
    socket?.close()
  }
}
