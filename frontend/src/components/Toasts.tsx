import { dismissToast, useStore } from '../state/store'

const STYLES: Record<string, string> = {
  info: 'border-line bg-ink-700',
  ok: 'border-emerald-800 bg-emerald-950/70',
  warn: 'border-amber-800 bg-amber-950/70',
  error: 'border-red-800 bg-red-950/70',
}
const ICON: Record<string, string> = { info: 'i', ok: '✓', warn: '!', error: '×' }

export default function Toasts() {
  const toasts = useStore((s) => s.toasts)
  if (!toasts.length) return null
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-md">
      {toasts.map((t) => (
        <div key={t.id}
             className={`card border px-3.5 py-2.5 shadow-xl ${STYLES[t.kind]}`}
             onClick={() => dismissToast(t.id)} role="status">
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 w-4 h-4 shrink-0 rounded-full bg-black/30
                             text-[10px] flex items-center justify-center">
              {ICON[t.kind]}
            </span>
            <div className="min-w-0">
              <div className="text-sm font-medium">{t.title}</div>
              {t.detail && (
                <div className="text-xs text-slate-400 mt-0.5 whitespace-pre-wrap">
                  {t.detail}
                </div>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
