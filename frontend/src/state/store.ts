import { useSyncExternalStore } from 'react'
import type { Envelope, Job, Project, TimelineView } from '../types'

export interface Toast {
  id: number
  kind: 'info' | 'ok' | 'warn' | 'error'
  title: string
  detail?: string
}

export interface AppState {
  view: 'home' | 'editor'
  project: Project | null
  envelope: Envelope | null
  timeline: TimelineView | null
  words: any[]
  removedWordIds: number[]
  fillers: any[]
  jobs: Record<string, Job>
  activeJob: Job | null
  toasts: Toast[]
  selection: { start: number; end: number } | null
  selectedClip: string | null
  history: any[]
  future: any[]
  loading: boolean
}

const initial: AppState = {
  view: 'home',
  project: null,
  envelope: null,
  timeline: null,
  words: [],
  removedWordIds: [],
  fillers: [],
  jobs: {},
  activeJob: null,
  toasts: [],
  selection: null,
  selectedClip: null,
  history: [],
  future: [],
  loading: false,
}

let state: AppState = initial
const listeners = new Set<() => void>()

export function setState(patch: Partial<AppState> | ((s: AppState) => Partial<AppState>)) {
  const next = typeof patch === 'function' ? patch(state) : patch
  state = { ...state, ...next }
  listeners.forEach((l) => l())
}

export function getState(): AppState {
  return state
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useStore<T>(selector: (s: AppState) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state), () => selector(initial))
}

// ---------------------------------------------------------------- playhead
// O playhead muda 60x por segundo. Se ele morasse no AppState, cada quadro
// re-renderizava TODO componente inscrito na store — o painel de mídia, o
// inspetor, a lista de legendas — e a interface engasgava num vídeo longo.
// Ele tem canal próprio, e o canal avisa no máximo uma vez por quadro.
let playheadValue = 0
const playheadListeners = new Set<() => void>()
let playheadScheduled = false

export function getPlayhead(): number {
  return playheadValue
}

export function setPlayhead(t: number) {
  if (!Number.isFinite(t) || t === playheadValue) return
  playheadValue = t
  if (playheadScheduled) return
  playheadScheduled = true
  const flush = () => {
    playheadScheduled = false
    playheadListeners.forEach((l) => l())
  }
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(flush)
  else setTimeout(flush, 16)
}

/** Inscrição crua — para quem desenha em canvas sem passar pelo React. */
export function subscribePlayhead(listener: () => void): () => void {
  playheadListeners.add(listener)
  return () => playheadListeners.delete(listener)
}

/** Use só onde o valor aparece na tela. Em callback, prefira getPlayhead(). */
export function usePlayhead(): number {
  return useSyncExternalStore(subscribePlayhead, getPlayhead, () => 0)
}

let toastId = 0
export function toast(kind: Toast['kind'], title: string, detail?: string) {
  const id = ++toastId
  setState((s) => ({ toasts: [...s.toasts, { id, kind, title, detail }] }))
  window.setTimeout(() => {
    setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
  }, kind === 'error' ? 9000 : 4500)
}

export function dismissToast(id: number) {
  setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
}

export interface HistoryEntry {
  plan: any
  removedWordIds: number[]
  manualRemovedWordIds: number[]
}

/** Empilha plano + estado de remoções — o desfazer restaura os dois juntos. */
export function pushHistory(plan: any, removedWordIds: number[],
                            manualRemovedWordIds: number[]) {
  const entry: HistoryEntry = {
    plan: JSON.parse(JSON.stringify(plan)),
    removedWordIds: [...removedWordIds],
    manualRemovedWordIds: [...manualRemovedWordIds],
  }
  setState((s) => ({ history: [...s.history, entry].slice(-100), future: [] }))
}
