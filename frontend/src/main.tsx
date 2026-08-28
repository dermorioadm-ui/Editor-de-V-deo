import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// REDE DE SEGURANÇA DA JANELA INTEIRA.
//
// Soltar um arquivo em qualquer lugar que não seja um trilho fazia o navegador
// NAVEGAR PARA O ARQUIVO: a página do editor sumia e o trabalho não salvo ia
// junto. É um clique de distância — errar o trilho por dois pixels bastava.
// Aqui a janela recusa qualquer drop que ninguém tenha tratado; quem trata de
// verdade (o canvas da linha do tempo) já chamou preventDefault antes.
for (const evento of ['dragover', 'drop'] as const) {
  window.addEventListener(evento, (e: DragEvent) => {
    if (!e.dataTransfer?.types?.includes('Files')) return
    e.preventDefault()
    if (evento === 'dragover' && e.dataTransfer) e.dataTransfer.dropEffect = 'none'
  })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
