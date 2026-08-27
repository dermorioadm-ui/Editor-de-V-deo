import { useEffect } from 'react'
import Home from './components/Home'
import Editor from './components/Editor'
import Toasts from './components/Toasts'
import { connectJobs } from './lib/api'
import { setState, toast, useStore } from './state/store'

export default function App() {
  const view = useStore((s) => s.view)

  useEffect(() => {
    return connectJobs((job) => {
      setState((s) => {
        const jobs = { ...s.jobs, [job.id]: job }
        const active = job.status === 'rodando' || job.status === 'fila'
          ? job
          : (s.activeJob?.id === job.id ? job : s.activeJob)
        return { jobs, activeJob: active }
      })
      if (job.status === 'erro') toast('error', `${job.kind} falhou`, job.error)
    })
  }, [])

  return (
    <div className="h-full flex flex-col">
      {view === 'home' ? <Home /> : <Editor />}
      <Toasts />
    </div>
  )
}
