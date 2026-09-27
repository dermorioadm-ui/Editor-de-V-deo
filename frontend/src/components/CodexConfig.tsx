import { useEffect, useState } from 'react'
import { api } from '../lib/api'

export default function CodexConfig() {
  const [estado, setEstado] = useState<any>(null)
  const [caminho, setCaminho] = useState('')
  const [modelo, setModelo] = useState('')
  const [erro, setErro] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let vivo = true
    api.codexEstado().then(r => {
      if (vivo) { setEstado(r); setModelo(r.modelo || '') }
    }).catch(e => { if (vivo) setErro(e.message) })
    return () => { vivo = false }
  }, [])
  async function verificar() {
    setBusy(true); setErro('')
    try {
      const r = await api.codexConfig({ ...(caminho.trim() ? { caminho: caminho.trim() } : {}), modelo: modelo.trim() })
      setEstado(r)
    } catch (e: any) { setErro(e.message) }
    finally { setBusy(false) }
  }
  return <div className="space-y-2 text-xs" data-codex-config="1">
    <p className={estado?.logado && estado?.compativel ? 'text-emerald-300' : 'text-slate-300'} aria-live="polite">
      {!estado ? 'Verificando o Codex nesta máquina…'
        : estado.logado && estado.compativel ? `Conta do ChatGPT conectada · ${estado.versao}` : estado.motivo}
    </p>
    {estado && !estado.logado && <p className="text-slate-400">
      No PowerShell, execute <code>codex login</code> e entre com sua conta do ChatGPT.
      Depois, clique em verificar conexão.
    </p>}
    <details>
      <summary className="text-slate-400">Caminho e modelo (opcionais)</summary>
      <div className="space-y-2 mt-2">
        <label className="block">Executável do Codex
          <input className="field w-full" value={caminho} onChange={e => setCaminho(e.target.value)} placeholder={estado?.caminho || 'Detectar automaticamente'} />
        </label>
        <label className="block">Modelo disponível na sua conta
          <input className="field w-full" value={modelo} onChange={e => setModelo(e.target.value)} placeholder="Padrão do Codex" />
        </label>
      </div>
    </details>
    <button className="btn btn-xs" disabled={busy} onClick={verificar}>
      {busy ? 'verificando…' : 'verificar conexão'}</button>
    {erro && <p role="alert" className="text-amber-300">{erro}</p>}
    <p className="text-slate-500">Usa sua conta do ChatGPT. Transcrição e quadros de conferência são enviados à IA; a renderização fica neste computador.</p>
  </div>
}
