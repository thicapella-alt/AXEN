import { useEffect, useState, useCallback } from 'react'
import {
  getPrices,
  getRecommendations,
  dismissRecommendation,
  applyRecommendation,
} from './api.js'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtBRL(v) {
  if (v === null || v === undefined) return '—'
  return `R$ ${Number(v).toFixed(2).replace('.', ',')}`
}

function fmtPct(v, decimals = 1) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  return `${n > 0 ? '+' : ''}${n.toFixed(decimals)}%`
}

function gapColor(gap) {
  if (gap === null || gap === undefined) return '#718096'
  if (gap > 25) return '#c53030'
  if (gap > 10) return '#c05621'
  return '#276749'
}

function gapBadgeBg(gap) {
  if (gap === null || gap === undefined) return '#edf2f7'
  if (gap > 25) return '#fff5f5'
  if (gap > 10) return '#fffaf0'
  return '#f0fff4'
}

function priorityLabel(priority) {
  if (priority === 'high')   return { text: 'Alta',  color: '#c53030', bg: '#fff5f5' }
  if (priority === 'medium') return { text: 'Média', color: '#c05621', bg: '#fffaf0' }
  return                            { text: 'Baixa', color: '#276749', bg: '#f0fff4' }
}

// ── Shared UI primitives ──────────────────────────────────────────────────────

const cardStyle = {
  background: '#fff',
  borderRadius: 12,
  padding: '20px 24px',
  boxShadow: '0 1px 4px #0001',
  marginBottom: 24,
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
  padding: '12px 12px',
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

function ActionButton({ label, onClick, disabled, variant }) {
  const base = {
    padding: '5px 12px',
    borderRadius: 6,
    fontSize: 12,
    fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer',
    border: 'none',
    opacity: disabled ? 0.5 : 1,
    transition: 'opacity 0.15s',
  }
  const colors = variant === 'apply'
    ? { background: '#ebf8ff', color: '#2b6cb0' }
    : { background: '#edf2f7', color: '#4a5568' }
  return (
    <button style={{ ...base, ...colors }} onClick={onClick} disabled={disabled}>
      {label}
    </button>
  )
}

// ── Section: Price gap table (from /prices/) ──────────────────────────────────

function PriceGapTable({ prices, loading }) {
  if (loading) return <EmptyState msg="Carregando…" />
  if (!prices.length) return (
    <EmptyState msg="Nenhum dado de preço disponível. Execute o scraper." />
  )

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            {['Material', 'Preço mín. mercado', 'Ref. AXEN', 'Gap %', 'Situação'].map((h) => (
              <th key={h} style={thStyle}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...prices].sort((a, b) => (b.gap_pct ?? -Infinity) - (a.gap_pct ?? -Infinity)).map((p) => (
            <tr key={p.material}>
              <td style={{ ...tdBase, fontWeight: 600, textTransform: 'capitalize' }}>
                {p.material}
              </td>
              <td style={tdBase}>{fmtBRL(p.market_min)}</td>
              <td style={tdBase}>{fmtBRL(p.axen_ref)}</td>
              <td style={{
                ...tdBase,
                fontWeight: 700,
                color: gapColor(p.gap_pct),
              }}>
                <span style={{
                  background: gapBadgeBg(p.gap_pct),
                  padding: '2px 8px',
                  borderRadius: 12,
                  fontSize: 12,
                }}>
                  {fmtPct(p.gap_pct)}
                </span>
              </td>
              <td style={tdBase}>
                {p.gap_pct === null
                  ? <span style={{ color: '#a0aec0', fontSize: 12 }}>Sem referência</span>
                  : p.gap_pct > 25
                    ? <span style={{ color: '#c53030', fontSize: 12 }}>⚠️ Acima do limite</span>
                    : p.gap_pct > 10
                      ? <span style={{ color: '#c05621', fontSize: 12 }}>⚡ Monitorar</span>
                      : <span style={{ color: '#276749', fontSize: 12 }}>✓ Competitivo</span>
                }
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Section: Recommendation action panel ──────────────────────────────────────

function RecommendationPanel({ recs, onDismiss, onApply, actionLoading }) {
  if (!recs.length) return (
    <EmptyState msg="Nenhuma sugestão de preço ativa no momento. Os agentes não detectaram gaps significativos." />
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {recs.map((rec) => {
        const p   = priorityLabel(rec.priority)
        const data = rec.data_json || {}
        const busy = actionLoading === rec.id
        return (
          <div key={rec.id} style={{
            border: '1px solid #e2e8f0',
            borderLeft: `4px solid ${p.color}`,
            borderRadius: 8,
            padding: '14px 16px',
            background: '#fafafa',
          }}>
            {/* Header row */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1 }}>
                <span style={{
                  display: 'inline-block',
                  background: p.bg,
                  color: p.color,
                  fontSize: 11,
                  fontWeight: 700,
                  padding: '2px 8px',
                  borderRadius: 10,
                  marginBottom: 6,
                  textTransform: 'uppercase',
                  letterSpacing: 0.5,
                }}>
                  {p.text} prioridade
                </span>
                <div style={{ fontSize: 14, fontWeight: 600, color: '#1a202c', marginBottom: 4 }}>
                  {rec.title}
                </div>
                <div style={{ fontSize: 13, color: '#4a5568', lineHeight: 1.5 }}>
                  {rec.body}
                </div>
              </div>

              {/* Action buttons */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flexShrink: 0 }}>
                <ActionButton
                  label={busy ? '…' : '✓ Aplicar'}
                  variant="apply"
                  disabled={!!actionLoading}
                  onClick={() => onApply(rec.id)}
                />
                <ActionButton
                  label={busy ? '…' : '✕ Dispensar'}
                  variant="dismiss"
                  disabled={!!actionLoading}
                  onClick={() => onDismiss(rec.id)}
                />
              </div>
            </div>

            {/* Data pills */}
            {(data.material || data.market_min || data.axen_ref_price || data.gap_pct) && (
              <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
                {data.material && (
                  <span style={pillStyle('#edf2f7', '#4a5568')}>
                    Material: <strong>{data.material}</strong>
                  </span>
                )}
                {data.market_min !== undefined && (
                  <span style={pillStyle('#ebf8ff', '#2b6cb0')}>
                    Mín. mercado: <strong>{fmtBRL(data.market_min)}</strong>
                  </span>
                )}
                {data.axen_ref_price !== undefined && (
                  <span style={pillStyle('#e9d8fd', '#553c9a')}>
                    AXEN ref.: <strong>{fmtBRL(data.axen_ref_price)}</strong>
                  </span>
                )}
                {data.gap_pct !== undefined && (
                  <span style={pillStyle(gapBadgeBg(data.gap_pct), gapColor(data.gap_pct))}>
                    Gap: <strong>{fmtPct(data.gap_pct)}</strong>
                  </span>
                )}
                {data.price_changes_count !== undefined && (
                  <span style={pillStyle('#fefcbf', '#744210')}>
                    Variações (7d): <strong>{data.price_changes_count}</strong>
                  </span>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function pillStyle(bg, color) {
  return {
    background: bg,
    color,
    fontSize: 12,
    padding: '3px 10px',
    borderRadius: 12,
    display: 'inline-block',
  }
}

// ── Main component ────────────────────────────────────────────────────────────

export default function GestorPrecos() {
  const [prices, setPrices]           = useState([])
  const [recs, setRecs]               = useState([])
  const [loadingPrices, setLp]        = useState(true)
  const [loadingRecs, setLr]          = useState(true)
  const [errorPrices, setErrorPrices] = useState(null)
  const [errorRecs, setErrorRecs]     = useState(null)
  const [actionLoading, setAction]    = useState(null)  // rec.id being acted on

  // ── Fetch helpers ────────────────────────────────────────────────────────

  const fetchPrices = useCallback(() => {
    setLp(true)
    getPrices()
      .then(setPrices)
      .catch(setErrorPrices)
      .finally(() => setLp(false))
  }, [])

  const fetchRecs = useCallback(() => {
    setLr(true)
    getRecommendations({ type: 'price_suggestion', active_only: true, limit: 100 })
      .then(setRecs)
      .catch(setErrorRecs)
      .finally(() => setLr(false))
  }, [])

  useEffect(() => { fetchPrices(); fetchRecs() }, [fetchPrices, fetchRecs])

  // ── Actions ──────────────────────────────────────────────────────────────

  const handleDismiss = useCallback(async (id) => {
    setAction(id)
    try {
      await dismissRecommendation(id)
      fetchRecs()
    } catch {
      // Silently ignore — user can retry
    } finally {
      setAction(null)
    }
  }, [fetchRecs])

  const handleApply = useCallback(async (id) => {
    setAction(id)
    try {
      await applyRecommendation(id)
      fetchRecs()
    } catch {
      // Silently ignore — user can retry
    } finally {
      setAction(null)
    }
  }, [fetchRecs])

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: '#1a202c', marginBottom: 4 }}>
        Gestor de Preços
      </h1>
      <p style={{ color: '#718096', fontSize: 14, marginBottom: 28 }}>
        Gap competitivo por material e sugestões de ajuste de preço
      </p>

      {errorPrices && <ErrorBanner msg="Não foi possível carregar os dados de preço." />}
      {errorRecs   && <ErrorBanner msg="Não foi possível carregar as sugestões de preço." />}

      {/* ── Price gap table ── */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748' }}>
            Gap por material
          </h2>
          <button
            onClick={fetchPrices}
            disabled={loadingPrices}
            style={{
              fontSize: 12, color: '#3182ce', background: 'none',
              border: 'none', cursor: 'pointer', padding: '4px 8px',
            }}
          >
            ↻ Atualizar
          </button>
        </div>
        <PriceGapTable prices={prices} loading={loadingPrices} />
      </div>

      {/* ── Recommendation actions ── */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <div>
            <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', marginBottom: 2 }}>
              Sugestões de ajuste de preço
            </h2>
            <p style={{ fontSize: 12, color: '#a0aec0' }}>
              Recomendações ativas geradas pelo agente de preços
            </p>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {recs.length > 0 && (
              <span style={{
                background: '#fff5f5', color: '#c53030',
                fontSize: 12, fontWeight: 600,
                padding: '2px 10px', borderRadius: 10,
              }}>
                {recs.length} ativa{recs.length !== 1 ? 's' : ''}
              </span>
            )}
            <button
              onClick={fetchRecs}
              disabled={loadingRecs}
              style={{
                fontSize: 12, color: '#3182ce', background: 'none',
                border: 'none', cursor: 'pointer', padding: '4px 8px',
              }}
            >
              ↻ Atualizar
            </button>
          </div>
        </div>

        {loadingRecs
          ? <EmptyState msg="Carregando…" />
          : (
            <RecommendationPanel
              recs={recs}
              onDismiss={handleDismiss}
              onApply={handleApply}
              actionLoading={actionLoading}
            />
          )
        }
      </div>
    </div>
  )
}
