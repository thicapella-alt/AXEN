import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import NavMenu from './NavMenu.jsx'

const Inicio         = lazy(() => import('./Inicio.jsx'))
const Dashboard      = lazy(() => import('./Dashboard.jsx'))
const Competitividade = lazy(() => import('./Competitividade.jsx'))
const GestorPrecos   = lazy(() => import('./GestorPrecos.jsx'))
const Anuncios       = lazy(() => import('./Anuncios.jsx'))
const Vendas         = lazy(() => import('./Vendas.jsx'))
const Recomendacoes  = lazy(() => import('./Recomendacoes.jsx'))

function PageLoader() {
  return <div style={{ padding: 40, color: '#a0aec0', fontSize: 14 }}>Carregando…</div>
}

const layoutStyle = {
  display: 'flex',
  minHeight: '100vh',
  fontFamily: "'Inter', 'Segoe UI', system-ui, sans-serif",
  background: '#f4f6fb',
}

const mainStyle = {
  flex: 1,
  padding: '32px 36px',
  overflowY: 'auto',
}

export default function App() {
  return (
    <BrowserRouter>
      <div style={layoutStyle}>
        <NavMenu />
        <main style={mainStyle}>
          <Suspense fallback={<PageLoader />}>
            <Routes>
              <Route path="/inicio"          element={<Inicio />} />
              <Route path="/"               element={<Dashboard />} />
              <Route path="/competitividade" element={<Competitividade />} />
              <Route path="/precos"          element={<GestorPrecos />} />
              <Route path="/anuncios"        element={<Anuncios />} />
              <Route path="/vendas"          element={<Vendas />} />
              <Route path="/recomendacoes"   element={<Recomendacoes />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </BrowserRouter>
  )
}
