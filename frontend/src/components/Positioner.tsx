import { useCallback, useEffect, useRef, useState } from 'react'
import { clamp } from '../lib/format'

interface Props {
  frameUrl: string
  x: number
  y: number
  label?: string
  aspect?: number            // largura/altura do vídeo
  blocked?: { top: number; bottom: number } | null
  onChange: (x: number, y: number) => void
  onCommit?: (x: number, y: number) => void
}

const SNAPS = [0.25, 1 / 3, 0.5, 2 / 3, 0.75]
const TOL = 0.018

/**
 * Posição livre com guias de alinhamento (Parte 8).
 *
 * Arraste o marcador sobre o quadro. Ele encosta nas linhas de terço e no
 * centro, e a faixa da legenda queimada é bloqueada.
 */
export default function Positioner({ frameUrl, x, y, label, aspect = 9 / 16,
                                     blocked, onChange, onCommit }: Props) {
  const box = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)
  const [guides, setGuides] = useState<{ v: number | null; h: number | null }>(
    { v: null, h: null })

  const move = useCallback((clientX: number, clientY: number) => {
    const el = box.current
    if (!el) return
    const r = el.getBoundingClientRect()
    let nx = clamp((clientX - r.left) / r.width, 0, 1)
    let ny = clamp((clientY - r.top) / r.height, 0, 1)
    let gv: number | null = null
    let gh: number | null = null
    for (const s of SNAPS) {
      if (Math.abs(nx - s) < TOL) { nx = s; gv = s }
      if (Math.abs(ny - s) < TOL) { ny = s; gh = s }
    }
    if (blocked && ny > blocked.top && ny < blocked.bottom) {
      // empurra para fora da faixa da legenda queimada
      ny = (ny - blocked.top) < (blocked.bottom - ny)
        ? Math.max(0, blocked.top - 0.01)
        : Math.min(1, blocked.bottom + 0.01)
    }
    setGuides({ v: gv, h: gh })
    onChange(Number(nx.toFixed(4)), Number(ny.toFixed(4)))
  }, [onChange, blocked])

  useEffect(() => {
    if (!dragging) return
    const onMove = (e: MouseEvent) => move(e.clientX, e.clientY)
    const onUp = () => {
      setDragging(false)
      setGuides({ v: null, h: null })
      onCommit?.(x, y)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [dragging, move, onCommit, x, y])

  return (
    <div className="inline-block">
      <div ref={box}
           className="relative bg-black rounded border border-line overflow-hidden
                      cursor-crosshair select-none"
           style={{ width: 150, height: 150 / aspect }}
           onMouseDown={(e) => { setDragging(true); move(e.clientX, e.clientY) }}>
        <img src={frameUrl} alt="" draggable={false}
             className="absolute inset-0 w-full h-full object-cover opacity-70" />

        {/* terços */}
        {[1 / 3, 2 / 3].map((t) => (
          <div key={`v${t}`} className="absolute top-0 bottom-0 w-px bg-white/15"
               style={{ left: `${t * 100}%` }} />
        ))}
        {[1 / 3, 2 / 3].map((t) => (
          <div key={`h${t}`} className="absolute left-0 right-0 h-px bg-white/15"
               style={{ top: `${t * 100}%` }} />
        ))}

        {/* faixa bloqueada */}
        {blocked && (
          <div className="absolute left-0 right-0 bg-red-500/20 border-y
                          border-dashed border-red-400/60"
               style={{ top: `${blocked.top * 100}%`,
                        height: `${(blocked.bottom - blocked.top) * 100}%` }} />
        )}

        {/* guias ativas */}
        {guides.v !== null && (
          <div className="absolute top-0 bottom-0 w-px bg-accent"
               style={{ left: `${guides.v * 100}%` }} />
        )}
        {guides.h !== null && (
          <div className="absolute left-0 right-0 h-px bg-accent"
               style={{ top: `${guides.h * 100}%` }} />
        )}

        {/* marcador */}
        <div className="absolute w-5 h-5 -ml-2.5 -mt-2.5 rounded-full border-2
                        border-accent bg-accent/30 pointer-events-none"
             style={{ left: `${x * 100}%`, top: `${y * 100}%` }} />
        {label && (
          <div className="absolute px-1 py-0.5 rounded bg-accent text-ink-900
                          text-[9px] font-bold pointer-events-none whitespace-nowrap
                          -translate-x-1/2"
               style={{ left: `${x * 100}%`, top: `calc(${y * 100}% + 12px)` }}>
            {label}
          </div>
        )}
      </div>
      <div className="text-[10px] font-mono text-slate-500 mt-1 text-center">
        x {(x * 100).toFixed(1)}% · y {(y * 100).toFixed(1)}%
      </div>
    </div>
  )
}
