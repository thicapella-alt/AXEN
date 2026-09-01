import { useEffect, useState, useMemo } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend,
  ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { getSales, getSalesByState } from './api.js'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtBRL(v) {
  if (v === null || v === undefined) return '—'
  return `R$ ${Number(v).toFixed(2).replace('.', ',')}`
}

function fmtBRLShort(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (n >= 1000) return `R$ ${(n / 1000).toFixed(1)}k`
  return `R$ ${n.toFixed(0)}`
}

function fmtInt(v) {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('pt-BR')
}

// ── Shared UI primitives ──────────────────────────────────────────────────────

const cardStyle = {
  background: '#fff',
  borderRadius: 12,
  padding: '20px 24px',
  boxShadow: '0 1px 4px #0001',
  marginBottom: 24,
}

const selectStyle = {
  border: '1px solid #e2e8f0',
  borderRadius: 6,
  padding: '6px 10px',
  fontSize: 13,
  color: '#2d3748',
  background: '#fff',
  cursor: 'pointer',
}

const thStyle = {
  textAlign: 'left',
  padding: '10px 12px',
  fontSize: 12,
  fontWeight: 600,
  color: '#718096',
  textTransform: 'uppercase',
  letterSpacing: 0.6,
  borderBottom: '2px solid #e2e8f0',
  whiteSpace: 'nowrap',
}

const tdBase = {
  padding: '11px 12px',
  fontSize: 13,
  color: '#2d3748',
  borderBottom: '1px solid #f0f0f0',
  verticalAlign: 'middle',
}

function ErrorBanner({ msg }) {
  return (
    <div style={{
      background: '#fff5f5', border: '1px solid #feb2b2', borderRadius: 8,
      padding: '10px 16px', marginBottom: 20, color: '#c53030', fontSize: 13,
    }}>
      ⚠️ {msg}
    </div>
  )
}

function EmptyState({ msg }) {
  return (
    <div style={{ textAlign: 'center', color: '#a0aec0', padding: '40px 0', fontSize: 14 }}>
      {msg}
    </div>
  )
}

// ── Summary card ──────────────────────────────────────────────────────────────

function SummaryCard({ label, value, sub }) {
  return (
    <div style={{
      background: '#fff', borderRadius: 10, padding: '18px 20px',
      boxShadow: '0 1px 4px #0001', flex: 1, minWidth: 160,
    }}>
      <div style={{ fontSize: 12, color: '#718096', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color: '#1a202c' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: '#a0aec0', marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

// ── Top products table ────────────────────────────────────────────────────────

function TopProductsTable({ products, loading }) {
  if (loading) return <EmptyState msg="Carregando…" />
  if (!products.length) return (
    <EmptyState msg="Sem dados de venda no período. Sincronize via integrações." />
  )

  const maxQty = Math.max(...products.map((p) => p.total_units || 0), 1)

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            {['#', 'Produto / SKU', 'Material', 'Qtd. vendida', 'Receita total', 'Ticket médio'].map((h) => (
              <th key={h} style={thStyle}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {products.map((p, i) => {
            const pct = ((p.total_units || 0) / maxQty) * 100
            return (
              <tr key={i}>
                <td style={{ ...tdBase, color: '#a0aec0', width: 32 }}>{i + 1}</td>
                <td style={{ ...tdBase, fontWeight: 600, maxWidth: 200 }}>
                  <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {p.product_name || p.sku || '—'}
                  </div>
                  {p.sku && p.product_name && (
                    <div style={{ fontSize: 11, color: '#a0aec0', marginTop: 1 }}>{p.sku}</div>
                  )}
                </td>
                <td style={tdBase}>{p.material || '—'}</td>
                <td style={{ ...tdBase }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{
                      height: 6, width: `${Math.max(pct, 4)}%`, maxWidth: 80,
                      background: '#3182ce', borderRadius: 3, flexShrink: 0,
                    }} />
                    <span>{fmtInt(p.total_units)}</span>
                  </div>
                </td>
                <td style={{ ...tdBase, fontWeight: 600 }}>{fmtBRL(p.total_revenue)}</td>
                <td style={tdBase}>
                  {p.total_units ? fmtBRL(p.total_revenue / p.total_units) : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Weekly bar chart ──────────────────────────────────────────────────────────

function WeeklyChart({ weekly, loading }) {
  if (loading) return <EmptyState msg="Carregando…" />
  if (!weekly.length) return (
    <EmptyState msg="Sem dados semanais no período." />
  )

  const data = weekly.map((w) => ({
    semana: w.week_label || w.week_start || '—',
    Receita: Number(w.total_revenue || 0),
    Pedidos: Number(w.total_units   || 0),
  }))

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
        <XAxis
          dataKey="semana"
          tick={{ fontSize: 11 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          yAxisId="revenue"
          tickFormatter={fmtBRLShort}
          tick={{ fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={56}
        />
        <YAxis
          yAxisId="orders"
          orientation="right"
          tick={{ fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={36}
        />
        <Tooltip
          formatter={(value, name) =>
            name === 'Receita' ? [fmtBRL(value), name] : [fmtInt(value), name]
          }
          contentStyle={{ fontSize: 12, borderRadius: 8 }}
        />
        <Legend wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
        <Bar yAxisId="revenue" dataKey="Receita" fill="#3182ce" radius={[4, 4, 0, 0]} />
        <Bar yAxisId="orders"  dataKey="Pedidos" fill="#a0aec0" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

// ── By-state table ────────────────────────────────────────────────────────────

function ByStateTable({ rows, loading }) {
  if (loading) return <EmptyState msg="Carregando…" />
  if (!rows.length) return (
    <EmptyState msg="Sem dados geográficos no período." />
  )

  const maxRev = Math.max(...rows.map((r) => r.total_revenue || 0), 1)

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            {['Estado', 'Unidades', 'Receita', 'Participação'].map((h) => (
              <th key={h} style={thStyle}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const pct = ((r.total_revenue || 0) / maxRev) * 100
            return (
              <tr key={i}>
                <td style={{ ...tdBase, fontWeight: 600 }}>{r.buyer_state || '—'}</td>
                <td style={tdBase}>{fmtInt(r.total_units)}</td>
                <td style={{ ...tdBase, fontWeight: 600 }}>{fmtBRL(r.total_revenue)}</td>
                <td style={{ ...tdBase, width: 160 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{
                      height: 6,
                      width: `${Math.max(pct, 2)}%`,
                      maxWidth: 80,
                      background: '#38a169',
                      borderRadius: 3,
                      flexShrink: 0,
                    }} />
                    <span style={{ fontSize: 12, color: '#718096' }}>
                      {pct.toFixed(1)}%
                    </span>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

const PERIOD_OPTIONS = [
  { label: '7 dias',  days: 7 },
  { label: '30 dias', days: 30 },
  { label: '90 dias', days: 90 },
]

export default function Vendas() {
  const [days, setDays]               = useState(30)
  const [sales, setSales]             = useState(null)
  const [byState, setByState]         = useState([])
  const [loadingSales, setLoadingSales] = useState(true)
  const [loadingState, setLoadingState] = useState(true)
  const [errorSales, setErrorSales]   = useState(null)
  const [errorState, setErrorState]   = useState(null)

  // ── Fetches ──────────────────────────────────────────────────────────────

  useEffect(() => {
    setLoadingSales(true); setErrorSales(null)
    getSales({ days })
      .then(setSales)
      .catch(() => setErrorSales('Não foi possível carregar os dados de vendas.'))
      .finally(() => setLoadingSales(false))
  }, [days])

  useEffect(() => {
    setLoadingState(true); setErrorState(null)
    getSalesByState({ days })
      .then(setByState)
      .catch(() => setErrorState('Não foi possível carregar os dados por estado.'))
      .finally(() => setLoadingState(false))
  }, [days])

  // ── Derived summary ──────────────────────────────────────────────────────

  const summary = useMemo(() => {
    if (!sales) return { totalRevenue: null, totalOrders: null, avgTicket: null }
    const products = sales.top_products || []
    const totalRevenue = products.reduce((s, p) => s + (p.total_revenue || 0), 0)
    const totalOrders  = products.reduce((s, p) => s + (p.total_units  || 0), 0)
    return {
      totalRevenue,
      totalOrders,
      avgTicket: totalOrders > 0 ? totalRevenue / totalOrders : null,
    }
  }, [sales])

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: '#1a202c', marginBottom: 4 }}>
        Vendas
      </h1>
      <p style={{ color: '#718096', fontSize: 14, marginBottom: 28 }}>
        Desempenho de vendas consolidado por período
      </p>

      {/* ── Period selector ── */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
        {PERIOD_OPTIONS.map(({ label, days: d }) => (
          <button
            key={d}
            onClick={() => setDays(d)}
            style={{
              padding: '6px 16px', borderRadius: 20, fontSize: 13, fontWeight: 600,
              border: 'none', cursor: 'pointer', transition: 'all 0.15s',
              background: days === d ? '#2b6cb0' : '#edf2f7',
              color:      days === d ? '#fff'    : '#4a5568',
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {errorSales && <ErrorBanner msg={errorSales} />}
      {errorState && <ErrorBanner msg={errorState} />}

      {/* ── Summary cards ── */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 24, flexWrap: 'wrap' }}>
        <SummaryCard
          label="Receita total"
          value={loadingSales ? '…' : fmtBRL(summary.totalRevenue)}
          sub={`últimos ${days} dias`}
        />
        <SummaryCard
          label="Unidades vendidas"
          value={loadingSales ? '…' : fmtInt(summary.totalOrders)}
          sub="soma dos top produtos"
        />
        <SummaryCard
          label="Ticket médio"
          value={loadingSales ? '…' : fmtBRL(summary.avgTicket)}
          sub="receita ÷ unidades"
        />
        <SummaryCard
          label="Semanas analisadas"
          value={loadingSales ? '…' : Math.max(1, Math.ceil(days / 7))}
          sub="no período selecionado"
        />
      </div>

      {/* ── Weekly chart ── */}
      <div style={cardStyle}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', marginBottom: 4 }}>
          Evolução semanal
        </h2>
        <p style={{ fontSize: 12, color: '#a0aec0', marginBottom: 20 }}>
          Receita e número de pedidos por semana
        </p>
        <WeeklyChart weekly={sales?.weekly || []} loading={loadingSales} />
      </div>

      {/* ── Top products ── */}
      <div style={cardStyle}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', marginBottom: 16 }}>
          Top produtos
        </h2>
        <TopProductsTable products={sales?.top_products || []} loading={loadingSales} />
      </div>

      {/* ── By state ── */}
      <div style={cardStyle}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', marginBottom: 16 }}>
          Vendas por estado
        </h2>
        <ByStateTable rows={byState} loading={loadingState} />
      </div>
    </div>
  )
}
