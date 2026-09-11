import { NavLink } from 'react-router-dom'

const LINKS = [
  { to: '/inicio',          label: '🏠 Início' },
  { to: '/',               label: '📊 Acompanhamento de Preços' },
  { to: '/competitividade', label: '📁 Base Histórica de Preços' },
  { to: '/precos',          label: '💰 Gestor de Preços' },
  { to: '/anuncios',        label: '📢 Anúncios ML' },
  { to: '/vendas',          label: '🛒 Vendas' },
  { to: '/recomendacoes',   label: '💡 Recomendações' },
]

const navStyle = {
  width: 220,
  minHeight: '100vh',
  background: '#1a1a2e',
  color: '#eee',
  display: 'flex',
  flexDirection: 'column',
  padding: '24px 0',
  flexShrink: 0,
}

const brandStyle = {
  fontSize: 20,
  fontWeight: 700,
  letterSpacing: 1,
  padding: '0 20px 24px',
  borderBottom: '1px solid #ffffff22',
  color: '#f0c040',
}

const linkStyle = {
  display: 'block',
  padding: '12px 20px',
  color: '#ccc',
  textDecoration: 'none',
  fontSize: 14,
  borderLeft: '3px solid transparent',
  transition: 'background 0.15s, color 0.15s',
}

const activeLinkStyle = {
  ...linkStyle,
  background: '#ffffff12',
  color: '#f0c040',
  borderLeft: '3px solid #f0c040',
}

export default function NavMenu() {
  return (
    <nav style={navStyle}>
      <div style={brandStyle}>AXEN Intelligence</div>
      {LINKS.map(({ to, label }) => (
        <NavLink
          key={to}
          to={to}
          end={to === '/'}
          style={({ isActive }) => (isActive ? activeLinkStyle : linkStyle)}
        >
          {label}
        </NavLink>
      ))}
    </nav>
  )
}
