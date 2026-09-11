import { useEffect, useState, useMemo } from 'react'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, Legend,
  ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { getCompetitors, getPrices, getPriceHistory } from './api.js'

// ── Palette ───────────────────────────────────────────────────────────────────

const GAP_COLORS = {
  high:   { bg: '#fff5f5', border: '#feb2b2', text: '#c53030' },
  medium: { bg: '#fffaf0', border: '#fbd38d', text: '#c05621' },
  ok:     { bg: '#f0fff4', border: '#9ae6b4', text: '#276749' },
  none:   { bg: '#f7fafc', border: '#e2e8f0', text: '#4a5568' },
}

const LINE_PALETTE = [
  '#3182ce', '#dd6b20', '#38a169', '#805ad5',
  '#d53f8c', '#319795', '#e53e3e', '#d69e2e',
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function gapLevel(gap) {
  if (gap === null || gap === undefined) return 'none'
  if (gap > 25) return 'high'
  if (gap > 10) return 'medium'
  if (gap > 0)  return 'ok'
  return 'none'
}

function fmtBRL(v) {
  if (v === null || v === undefined || v === '') return '—'
  return `R$ ${Number(v).toFixed(2).replace('.', ',')}`
}

function fmtPct(v, decimals = 1) {
  if (v === null || v === undefined) return '—'
  return `${Number(v).toFixed(decimals)}%`
}

function sortedUnique(arr) {
  return [...new Set(arr.filter(Boolean))].sort()
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
  borderBottom: '1px solid #e2e8f0',
  whiteSpace: 'nowrap',
}

function tdStyle(level) {
  const c = GAP_COLORS[level]
  return {
    padding: '10px 12px',
    fontSize: 13,
    color: '#2d3748',
    borderBottom: '1px solid #f0f0f0',
    background: c.bg,
  }
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

// ── History chart helpers ─────────────────────────────────────────────────────

/**
 * Transform price-change event rows into a series suitable for recharts.
 * Each point: { date, [store]: price_after }
 * We pivot by store so each store gets its own Line.
 */
function buildChartData(historyRows, materialFilter) {
  const rows = materialFilter
    ? historyRows.filter((r) => r.material === materialFilter)
    : historyRows

  if (!rows.length) return { data: [], stores: [] }

  // Collect all stores
  const storeSet = new Set(rows.map((r) => r.store))
  const stores   = [...storeSet].sort()

  // Group by date (date part of changed_at)
  const byDate = {}
  for (const row of rows) {
    const date = (row.changed_at || '').slice(0, 10)
    if (!date) continue
    if (!byDate[date]) byDate[date] = { date }
    byDate[date][row.store] = row.price_after
  }

  const data = Object.values(byDate).sort((a, b) => a.date.localeCompare(b.date))
  return { data, stores }
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Competitividade() {
  const [competitors, setCompetitors]     = useState([])
  const [prices, setPrices]               = useState([])
  const [history, setHistory]             = useState([])
  const [loadingComp, setLoadingComp]     = useState(true)
  const [loadingHist, setLoadingHist]     = useState(true)
  const [errorComp, setErrorComp]         = useState(null)
  const [errorHist, setErrorHist]         = useState(null)
  const [filterMaterial, setFilterMaterial] = useState('')
  const [filterStore, setFilterStore]       = useState('')

  // ── Fetch ────────────────────────────────────────────────────────────────

  useEffect(() => {
    setLoadingComp(true)
    Promise.all([getCompetitors(), getPrices()])
      .then(([comps, px]) => { setCompetitors(comps); setPrices(px) })
      .catch(setErrorComp)
      .finally(() => setLoadingComp(false))
  }, [])

  useEffect(() => {
    setLoadingHist(true)
    getPriceHistory({ days: 30 })
      .then(setHistory)
      .catch(setErrorHist)
      .finally(() => setLoadingHist(false))
  }, [])

  // ── Derived data ─────────────────────────────────────────────────────────

  // Build a quick lookup: material → axen_ref
  const axenRef = useMemo(() => {
    const map = {}
    for (const p of prices) map[p.material] = p.axen_ref
    return map
  }, [prices])

  // All distinct values for filter dropdowns
  const materials = useMemo(() => sortedUnique(competitors.map((c) => c.material)), [competitors])
  const stores    = useMemo(() => sortedUnique(competitors.map((c) => c.store)),    [competitors])

  // Filtered rows
  const rows = useMemo(() => {
    let list = competitors
    if (filterMaterial) list = list.filter((c) => c.material === filterMaterial)
    if (filterStore)    list = list.filter((c) => c.store    === filterStore)
    // Sort cheapest first within each material
    return [...list].sort((a, b) => a.material.localeCompare(b.material) || a.price - b.price)
  }, [competitors, filterMaterial, filterStore])

  // Compute gap for each row
  const rowsWithGap = useMemo(() =>
    rows.map((r) => {
      const ref = axenRef[r.material]
      const gap = ref !== null && ref !== undefined && r.price > 0
        ? (ref - r.price) / r.price * 100
        : null
      return { ...r, axen_ref: ref ?? null, gap_pct: gap }
    }),
  [rows, axenRef])

  // Chart data
  const { data: chartData, stores: chartStores } = useMemo(
    () => buildChartData(history, filterMaterial),
    [history, filterMaterial],
  )

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: '#1a202c', marginBottom: 4 }}>
        Base Histórica de Preços
      </h1>
      <p style={{ color: '#718096', fontSize: 14, marginBottom: 28 }}>
        Preços dos concorrentes coletados na última execução do scraper
      </p>

      {errorComp && <ErrorBanner msg="Não foi possível carregar os preços dos concorrentes." />}
      {errorHist && <ErrorBanner msg="Não foi possível carregar o histórico de preços." />}

      {/* ── Filters ── */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        <select style={selectStyle} value={filterMaterial} onChange={(e) => setFilterMaterial(e.target.value)}>
          <option value="">Todos os materiais</option>
          {materials.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select style={selectStyle} value={filterStore} onChange={(e) => setFilterStore(e.target.value)}>
          <option value="">Todas as lojas</option>
          {stores.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {(filterMaterial || filterStore) && (
          <button
            onClick={() => { setFilterMaterial(''); setFilterStore('') }}
            style={{
              ...selectStyle, background: '#edf2f7', border: '1px solid #cbd5e0',
              cursor: 'pointer', color: '#4a5568',
            }}
          >
            ✕ Limpar filtros
          </button>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 13, color: '#a0aec0', alignSelf: 'center' }}>
          {rowsWithGap.length} produto{rowsWithGap.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* ── Competitors table ── */}
      <div style={cardStyle}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', marginBottom: 16 }}>
          Produtos concorrentes
        </h2>

        {loadingComp ? (
          <EmptyState msg="Carregando…" />
        ) : rowsWithGap.length === 0 ? (
          <EmptyState msg="Nenhum produto encontrado. Execute o scraper ou ajuste os filtros." />
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  {['Loja', 'Produto', 'Preço', 'Material', 'Desconto', 'AXEN ref.', 'Gap', 'Link'].map((h) => (
                    <th key={h} style={thStyle}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rowsWithGap.map((row, i) => {
                  const level = gapLevel(row.gap_pct)
                  const c     = GAP_COLORS[level]
                  return (
                    <tr key={i}>
                      <td style={tdStyle(level)}>{row.store}</td>
                      <td style={{ ...tdStyle(level), maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {row.name}
                      </td>
                      <td style={{ ...tdStyle(level), fontWeight: 600 }}>
                        {fmtBRL(row.price)}
                      </td>
                      <td style={tdStyle(level)}>{row.material}</td>
                      <td style={tdStyle(level)}>
                        {row.discount_pct ? `${row.discount_pct}%` : '—'}
                      </td>
                      <td style={tdStyle(level)}>{fmtBRL(row.axen_ref)}</td>
                      <td style={{
                        ...tdStyle(level),
                        fontWeight: 600,
                        color: c.text,
                      }}>
                        {fmtPct(row.gap_pct)}
                      </td>
                      <td style={tdStyle(level)}>
                        {row.url ? (
                          <a href={row.url} target="_blank" rel="noreferrer"
                            style={{ color: '#3182ce', fontSize: 12 }}>
                            Ver →
                          </a>
                        ) : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Legend */}
        {!loadingComp && rowsWithGap.length > 0 && (
          <div style={{ display: 'flex', gap: 16, marginTop: 14, fontSize: 12, color: '#718096' }}>
            {[
              { level: 'high',   label: 'Gap > 25% (alta prioridade)' },
              { level: 'medium', label: 'Gap 10–25% (média prioridade)' },
              { level: 'ok',     label: 'Gap < 10% (dentro do limite)' },
              { level: 'none',   label: 'Sem referência AXEN' },
            ].map(({ level, label }) => (
              <span key={level} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{
                  width: 12, height: 12, borderRadius: 3,
                  background: GAP_COLORS[level].bg,
                  border: `1px solid ${GAP_COLORS[level].border}`,
                  display: 'inline-block',
                }} />
                {label}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* ── Price history chart ── */}
      <div style={cardStyle}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', marginBottom: 4 }}>
          Histórico de variações de preço (30 dias)
        </h2>
        <p style={{ fontSize: 12, color: '#a0aec0', marginBottom: 20 }}>
          Preço registrado após cada variação detectada pelo scraper
          {filterMaterial ? ` — material: ${filterMaterial}` : ''}
        </p>

        {loadingHist ? (
          <EmptyState msg="Carregando…" />
        ) : chartData.length === 0 ? (
          <EmptyState msg="Sem variações de preço registradas nos últimos 30 dias." />
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tickFormatter={(v) => `R$${v}`}
                tick={{ fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={56}
              />
              <Tooltip
                formatter={(value, name) => [fmtBRL(value), name]}
                labelFormatter={(label) => `Data: ${label}`}
                contentStyle={{ fontSize: 12, borderRadius: 8 }}
              />
              <Legend wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
              {chartStores.map((store, i) => (
                <Line
                  key={store}
                  type="monotone"
                  dataKey={store}
                  stroke={LINE_PALETTE[i % LINE_PALETTE.length]}
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
