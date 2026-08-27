export function timecode(seconds: number, withMs = false): string {
  if (!Number.isFinite(seconds)) return '00:00'
  const s = Math.max(0, seconds)
  const m = Math.floor(s / 60)
  const rest = s - m * 60
  const base = `${String(m).padStart(2, '0')}:${String(Math.floor(rest)).padStart(2, '0')}`
  if (!withMs) return base
  return `${base}.${String(Math.round((rest % 1) * 1000)).padStart(3, '0')}`
}

export function seconds(value: number, digits = 2): string {
  return `${value.toFixed(digits)} s`
}

export function bytes(value: number): string {
  if (!value) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = value
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1 }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function mbps(bits: number): string {
  return bits ? `${(bits / 1e6).toFixed(2)} Mbps` : '—'
}

export function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value))
}
