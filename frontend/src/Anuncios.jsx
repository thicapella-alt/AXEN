import { useEffect, useState } from 'react'
import { getAnunciosML } from './api.js'

function fmtBRL(v) {
  if (!v) return 'R$ 0,00'
  return `R$ ${Number(v).toFixed(2).replace('.', ',')}`
}

const card = {
  background: '#fff',
  borderRadius: 12,
  padding: '20px 24px',
  boxShadow: '0 1px 4px #0001',
  marginBottom: 24,
}

const th = {
  textAlign: 'left',
  padding: '10px 14px',
  fontSize: 11,
  fontWeight: 600,
  color: '#718096',
  textTransform: 'uppercase',
  letterSpacing: 0.6,
  borderBottom: '2px solid #e2e8f0',
  whiteSpace: 'nowrap',
}

const td = {
  padding: '12px 14px',
  fontSize: 13,
  color: '#2d3748',
  borderBottom: '1px solid #f7fafc',
  verticalAlign: 'middle',
}

function PeriodSelector({ value, onChange }) {
  return (
    <div style={{ display: 'flex', gap: 6 }}>
      {[7, 30, 90].map(d => (
        <button
          key={d}
          onClick={() => onChange(d)}
          style={{
            padding: '5px 14px', borderRadius: 99, fontSize: 12, fontWeight: 600,
            border: '1px solid #e2e8f0', cursor: 'pointer',
            background: value === d ? '#3182ce' : '#fff',
            color: value === d ? '#fff' : '#4a5568',
          }}
        >
          {d}d
        </button>
      ))}
    </div>
  )
}

function VisitBar({ visits, max }) {
  const pct = max > 0 ? Math.round(visits / max * 100) : 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ flex: 1, background: '#f7fafc', borderRadius: 99, height: 6, minWidth: 60 }}>
        <div style={{ width: `${pct}%`, height: '100%', background: '#f5a623', borderRadius: 99 }} />
      </div>
      <span style={{ fontSize: 13, fontWeight: 700, color: '#1a202c', width: 28, textAlign: 'right' }}>
        {visits}
      </span>
    </div>
  )
}

export default function Anuncios() {
  const [days, setDays]         = useState(30)
  const [listings, setListings] = useState([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    getAnunciosML(days)
      .then(d => setListings(d.listings ?? []))
      .catch(() => setError('Não foi possível carregar os anúncios.'))
      .finally(() => setLoading(false))
  }, [days])

  const maxVisits = listings.length > 0 ? listings[0].visits : 1
  const totalVisits = listings.reduce((s, l) => s + (l.visits || 0), 0)
  const totalOrders = listings.reduce((s, l) => s + (l.orders || 0), 0)
  const totalRevenue = listings.reduce((s, l) => s + (l.revenue || 0), 0)

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 28 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: '#1a202c', marginBottom: 4 }}>
            Anúncios ML
          </h1>
          <p style={{ color: '#718096', fontSize: 14 }}>
            Desempenho dos seus anúncios no Mercado Livre
          </p>
        </div>
        <PeriodSelector value={days} onChange={setDays} />
      </div>

      {error && (
        <div style={{
          background: '#fff5f5', border: '1px solid #feb2b2', borderRadius: 8,
          padding: '12px 16px', marginBottom: 24, color: '#c53030', fontSize: 13,
        }}>
          ⚠️ {error}
        </div>
      )}

      {/* Summary cards */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 28, flexWrap: 'wrap' }}>
        {[
          { label: 'Anúncios ativos', value: listings.length, icon: '📋' },
          { label: `Visitas (${days}d)`, value: totalVisits.toLocaleString('pt-BR'), icon: '👁' },
          { label: `Pedidos (${days}d)`, value: totalOrders, icon: '🛒' },
          { label: `Receita (${days}d)`, value: fmtBRL(totalRevenue), icon: '💰' },
        ].map(({ label, value, icon }) => (
          <div key={label} style={{
            background: '#fff', borderRadius: 10, padding: '18px 22px',
            boxShadow: '0 1px 4px #0001', flex: 1, minWidth: 160,
          }}>
            <div style={{ fontSize: 20, marginBottom: 8 }}>{icon}</div>
            <div style={{ fontSize: 11, color: '#718096', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>
              {label}
            </div>
            <div style={{ fontSize: 22, fontWeight: 700, color: '#1a202c' }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Listings table */}
      <div style={card}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', marginBottom: 16 }}>
          Performance por anúncio
        </h2>

        {loading ? (
          <div style={{ color: '#a0aec0', padding: '32px 0', textAlign: 'center' }}>
            Carregando anúncios…
          </div>
        ) : listings.length === 0 ? (
          <div style={{ color: '#a0aec0', padding: '32px 0', textAlign: 'center' }}>
            Nenhum dado encontrado. Sincronize para importar os anúncios.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={th}>Anúncio</th>
                  <th style={{ ...th, width: 220 }}>Visitas únicas</th>
                  <th style={{ ...th, textAlign: 'right' }}>Pedidos</th>
                  <th style={{ ...th, textAlign: 'right' }}>Receita</th>
                  <th style={{ ...th, textAlign: 'center' }}>Ver no ML</th>
                </tr>
              </thead>
              <tbody>
                {listings.map(l => (
                  <tr key={l.item_id} style={{ transition: 'background 0.1s' }}
                    onMouseEnter={e => e.currentTarget.style.background = '#f7fafc'}
                    onMouseLeave={e => e.currentTarget.style.background = ''}
                  >
                    <td style={td}>
                      <div style={{ fontWeight: 600, color: '#1a202c' }}>{l.title}</div>
                      <div style={{ fontSize: 11, color: '#a0aec0', marginTop: 2 }}>{l.item_id}</div>
                    </td>
                    <td style={td}>
                      <VisitBar visits={l.visits} max={maxVisits} />
                    </td>
                    <td style={{ ...td, textAlign: 'right', fontWeight: 600 }}>
                      {l.orders || '—'}
                    </td>
                    <td style={{ ...td, textAlign: 'right', color: l.revenue > 0 ? '#276749' : '#a0aec0', fontWeight: 600 }}>
                      {l.revenue > 0 ? fmtBRL(l.revenue) : '—'}
                    </td>
                    <td style={{ ...td, textAlign: 'center' }}>
                      <a
                        href={l.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          fontSize: 12, fontWeight: 600, color: '#2b6cb0',
                          background: '#ebf8ff', border: '1px solid #bee3f8',
                          borderRadius: 6, padding: '4px 12px', textDecoration: 'none',
                        }}
                      >
                        Abrir ↗
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
