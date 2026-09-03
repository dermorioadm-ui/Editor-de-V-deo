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
const patch = <T>(url: string, body?: unknown) =>
  req<T>(url, { method: 'PATCH', body: JSON.stringify(body ?? {}) })
const del = <T>(url: string) => req<T>(url, { method: 'DELETE' })

export const api = {
  health: () => req<any>('/api/health'),
  browse: (path: string) => req<any>(`/api/browse?path=${encodeURIComponent(path)}`),
  locate: (name: string, size: number) =>
    post<any>('/api/locate', { name, size }),

  // A JANELA DO SISTEMA — a de verdade. O servidor roda na máquina do usuário,
  // então ele pode abrir o mesmo diálogo de qualquer programa e devolver o
  // caminho. Nada é enviado: o que atravessa é uma string.
  janela: () => req<{ disponivel: boolean }>('/api/janela'),
  escolher: (kind: 'video' | 'audio' | 'image' | 'media' | 'texto',
             titulo?: string, varios = false) =>
    post<{ ok: boolean; cancelado: boolean; path: string; paths: string[] }>(
      '/api/escolher', { kind, titulo, varios }),

  projects: () => req<any[]>('/api/projects'),
  project: (id: string) => req<Project>(`/api/projects/${id}`),
  createProject: (source_path: string, name: string, preset: string) =>
    post<Project>('/api/projects', { source_path, name, preset }),
  deleteProject: (id: string) => del<any>(`/api/projects/${id}`),

  envelope: (id: string, points = 4000) =>
    req<Envelope>(`/api/projects/${id}/envelope?points=${points}`),

  analyze: (id: string) => post<Job>(`/api/projects/${id}/analyze`),
  autoedit: (id: string) => post<Job>(`/api/projects/${id}/autoedit`),
  // a receita vai JUNTO: aplicada depois do preset, no servidor, para não
  // ser apagada por ele (era o que acontecia com velocidade e zoom)
  oneclick: (id: string, preset?: string, receita?: any) =>
    post<Job>(`/api/projects/${id}/oneclick`, { preset, receita }),
  exportProject: (id: string, options: any) =>
    post<Job>(`/api/projects/${id}/export`, options),
  preview: (id: string, opts: { scale?: string; crf?: number } = {}) =>
    post<Job>(`/api/projects/${id}/preview`,
      { scale: opts.scale ?? '240', crf: opts.crf ?? 32 }),
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

  ajustarMusica: (id: string, patch: Record<string, number | boolean>) =>
    post<any>(`/api/projects/${id}/ops/music`, patch),

  buildProxy: (id: string) => post<any>(`/api/projects/${id}/proxy`),
  proxyStatus: (id: string) =>
    req<{ ok: boolean; precisa: boolean; detail: string; size_bytes: number }>(
      `/api/projects/${id}/proxy-status`),

  looks: () => req<any[]>('/api/looks'),
  setLook: (id: string, look: string, vignette?: number | null) =>
    post(`/api/projects/${id}/ops/look`,
         vignette === undefined ? { look } : { look, vignette }),

  setRepeat: (id: string, repeat_id: string, restored: boolean) =>
    post(`/api/projects/${id}/ops/repeat`, { repeat_id, restored }),

  // o quadro de um formato derivado (encaixe/recorte, tamanho, lugar, fundo)
  // encurtar o vídeo para caber numa duração — a IA escolhe o que sai (JOB)
  resumir: (id: string, alvo: number) =>
    post<Job>(`/api/projects/${id}/ops/resumir`, { alvo }),
  setQuadro: (id: string, aspecto: string, patch: any) =>
    post<any>(`/api/projects/${id}/ops/quadro`, { aspecto, ...patch }),
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

  addMedia: (id: string, path: string, kind: string, descricao = '') =>
    post<any>(`/api/projects/${id}/media`, { path, kind, descricao }),
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

  setWhistle: (id: string, wid: string, enabled: boolean) =>
    post<any>(`/api/projects/${id}/whistles/${wid}`, { enabled }),
  calibrateWhistle: (id: string) =>
    post<any>(`/api/projects/${id}/whistle/calibrate`),
  clearWhistleCalibration: (id: string) =>
    del<any>(`/api/projects/${id}/whistle/calibrate`),

  // IA — a chave NUNCA volta por aqui, só se ela existe e os quatro últimos
  // caracteres, o bastante para o usuário reconhecer qual chave está lá
  aiConfig: () => req<any>('/api/ai/config'),
  // a chave lida de um .txt que o usuário aponta — o servidor lê, testa e
  // guarda; a chave nunca volta na resposta
  chaveDeArquivo: (path: string) => post<any>('/api/ai/chave-de-arquivo', { path }),
  aiModelos: () => req<any>('/api/ai/modelos'),
  setAiConfig: (payload: any) => post<any>('/api/ai/config', payload),
  testAi: () => post<any>('/api/ai/test'),
  aiPlan: (id: string, anexos = true) =>
    post<Job>(`/api/projects/${id}/ai/plan`, { anexos }),
  compararModelos: (id: string, modelos: string[]) =>
    post<any>(`/api/projects/${id}/ai/comparar`, { modelos }),
  applyAiPlan: (id: string, plano: any) =>
    post<any>(`/api/projects/${id}/ai/apply`, { plano }),
  // gerar imagem (Nano Banana) ou vídeo (Veo) e pôr como janela no cursor.
  // É um JOB: a resposta é o job; a mídia aparece quando ele termina.
  gerarIa: (id: string, payload: { tipo: 'image' | 'video'; prompt: string;
                                   proporcao?: string; out_start?: number;
                                   duracao?: number; duracao_video?: number;
                                   colocar?: boolean }) =>
    post<Job>(`/api/projects/${id}/ai/gerar`, payload),
  mediaFileUrl: (id: string, mid: string) => `/api/projects/${id}/media/${mid}/file`,
  // cartões A PEDIDO (hook, tópicos, número) — JOB
  cartoesIa: (id: string, pedido: string) =>
    post<Job>(`/api/projects/${id}/ai/cartoes`, { pedido }),
  limparCartoes: (id: string) => del<any>(`/api/projects/${id}/cartoes`),
  // a biblioteca de músicas de fundo, que acumula a cada uma que entra
  musicas: () => req<any[]>('/api/musicas'),
  guardarMusica: (path: string) => post<any>('/api/musicas', { path }),
  apagarMusica: (mid: string) => del<any>(`/api/musicas/${mid}`),

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
  // a exportação AUTOMÁTICA: mesmo arquivo, sobrescrevendo, com cache
  exportFinal: (id: string) => post<Job>(`/api/projects/${id}/export-final`, {}),
  // o que é o arquivo, ANTES de criar o projeto — a primeira tela precisa da
  // proporção para oferecer os formatos extras que fazem sentido
  probe: (path: string) => post<any>('/api/probe', { path }),
  mediaDescricao: (id: string, mid: string, descricao: string) =>
    patch<any>(`/api/projects/${id}/media/${mid}`, { descricao }),
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
