export interface Word { i: number; start: number; end: number; text: string; prob: number }

export interface Clip {
  id: string; source: string; src_start: number; src_end: number; speed: number
  section: string; kind: string; audio: string; enabled: boolean
  cut_in: boolean; cut_out: boolean
  snap_in: SnapResult | null; snap_out: SnapResult | null
  label: string; photo: any; fit: any
  src_duration: number; out_duration: number; zoom: number
  out_start?: number; out_end?: number
}

export interface SnapResult {
  original: number; time: number; kind: string; found_valley: boolean
  valley_start: number | null; valley_end: number | null
  valley_duration: number | null; energy_db: number
  clamped_by_neighbor: boolean; reason: string; moved: number
}

export interface RemovedRegion {
  id: string; start: number; end: number; reason: string
  restorable: boolean; detail: string
}

export interface Clap {
  id: string; time: number; start: number; end: number; peak_db: number
  jump_db: number; duration: number; confirmed: boolean; suspect: boolean
  attack_floor_db: number; reason: string; enabled: boolean
  rise_ms: number; flatness: number; hf_ratio: number; timbre_score: number
}

export interface Take {
  id: string; start: number; end: number; clap_id: string | null
  clap_time: number | null; text: string; restored: boolean; reason: string
}

export interface SubtitleCue {
  id: string; start: number; end: number; text: string
  word_ids: number[]; edited: boolean
}

export interface AuditIssue {
  clip_id: string; side: string; time: number; severity: string
  level_db: number; threshold_db: number; message: string
  suggestion: number; suggestion_reason: string; word?: string
}

export interface Repeat {
  id: string; start: number; end: number; text: string
  kept_start: number; kept_end: number; kept_text: string
  similarity: number; word_ids: number[]; restored: boolean; reason: string
}

export interface WordFix {
  i: number; text: string; from: [number, number]; to: [number, number]
  ganho: number
}

export interface AuditFixed {
  clip_id: string; side: string; from: number; to: number
  reason: string; message: string; kind?: string
}

export interface Filler {
  id: string; word_ids: number[]; text: string; start: number; end: number
  safe: boolean; pause_before: number; pause_after: number; reason: string
}

export interface Envelope {
  hop: number; duration: number; noise_floor: number
  silence_threshold: number; speech_threshold: number
  audit_threshold: number; points: number[]
}

export interface TimelineView {
  duration: number; source_duration: number
  blocks: Clip[]; removed: RemovedRegion[]; takes: Take[]; claps: Clap[]
  subtitles: SubtitleCue[]; audit: AuditIssue[]; audit_fixed?: AuditFixed[]
  repeats?: Repeat[]; zoom?: { enabled: boolean; levels: number[]
                              max_level: number; bias_y: number }
  look?: string; look_vignette?: number | null; word_fixes?: WordFix[]
  cutaways: any[]; overlays: any[]; blurs: any[]; speed_warn: string[]
}

export interface MediaInfo {
  path: string; duration: number; width: number; height: number; fps: number
  v_codec: string; a_codec: string; bitrate: number; v_bitrate: number
  is_hdr: boolean; display_width: number; display_height: number
  size_bytes: number; rotation: number; has_audio: boolean
}

export interface Project {
  id: string; name: string; source_path: string; preset: string; status: string
  info: MediaInfo | null
  media: { id: string; path: string; kind: string; name: string; info: any }[]
  analysis?: any
  plan?: any
  timeline?: TimelineView
}

export interface Job {
  id: string; project_id: string; kind: string; status: string
  progress: number; stage: string; message: string; result: any
  error: string; created_at: number; updated_at: number
}

export const SECTIONS: Record<string, { label: string; color: string }> = {
  gancho: { label: 'Gancho', color: '#38bdf8' },
  dor: { label: 'Dor', color: '#fb923c' },
  explicacao: { label: 'Explicação', color: '#a78bfa' },
  revelacao: { label: 'Revelação', color: '#f472b6' },
  prova: { label: 'Prova', color: '#34d399' },
  oferta: { label: 'Oferta', color: '#facc15' },
  garantia: { label: 'Garantia', color: '#f87171' },
}
