import { useEffect, useState, useCallback } from 'react'
import {
  getRecommendations,
  dismissRecommendation,
  applyRecommendation,
  runAgents,
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

function fmtDate(s) {
  if (!s) return '—'
  const d = new Date(s)
  return isNaN(d) ? s : d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

function priorityMeta(priority) {
  if (priority === 'high')   return { text: 'Alta',  color: '#c53030', bg: '#fff5f5', border: '#feb2b2' }
  if (priority === 'medium') return { text: 'Média', color: '#c05621', bg: '#fffaf0', border: '#fbd38d' }
  return                            { text: 'Baixa', color: '#276749', bg: '#f0fff4', border: '#9ae6b4' }
}

const TYPE_LABELS = {
  price_suggestion:    'Preço',
  ml_position_drop:   'Posição ML',
  promo_suggestion:   'Promoção',
}

function typeLabel(t) {
  return TYPE_LABELS[t] || t
}

function typeColors(t) {
  if (t === 'price_suggestion')  return { bg: '#ebf8ff', color: '#2b6cb0' }
  if (t === 'ml_position_drop')  return { bg: '#e9d8fd', color: '#553c9a' }
  if (t === 'promo_suggestion')  return { bg: '#fefcbf', color: '#744210' }
  return { bg: '#edf2f7', color: '#4a5568' }
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

function Pill({ bg, color, children }) {
  return (
    <span style={{
      background: bg, color,
      fontSize: 12, padding: '3px 10px',
      borderRadius: 12, display: 'inline-block',
    }}>
      {children}
    </span>
  )
}

function ActionButton({ label, onClick, disabled, variant }) {
  const base = {
    padding: '5px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer',
    border: 'none', opacity: disabled ? 0.5 : 1, transition: 'opacity 0.15s',
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

// ── Agent run panel ───────────────────────────────────────────────────────────

function AgentResultItem({ result }) {
  const p = priorityMeta(result.priority)
  const tc = typeColors(result.type)
  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 10,
      padding: '10px 0', borderBottom: '1px solid #f0f0f0',
    }}>
      <Pill bg={tc.bg} color={tc.color}>{typeLabel(result.type)}</Pill>
      <Pill bg={p.bg} color={p.color}>{p.text}</Pill>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#1a202c' }}>{result.title}</div>
        <div style={{ fontSize: 12, color: '#4a5568', marginTop: 2 }}>{result.body}</div>
      </div>
    </div>
  )
}

function RunAgentsPanel({ onDone }) {
  const [running, setRunning]   = useState(false)
  const [results, setResults]   = useState(null)
  const [agent, setAgent]       = useState('all')
  const [error, setError]       = useState(null)

  const handleRun = async () => {
    setRunning(true); setError(null); setResults(null)
    try {
      const res = await runAgents(agent)
      setResults(res)
      onDone()  // refresh recommendation list
    } catch {
      setError('Falha ao executar os agentes. Tente novamente.')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div style={cardStyle}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', marginBottom: 2 }}>
            Executar agentes
          </h2>
          <p style={{ fontSize: 12, color: '#a0aec0' }}>
            Analisa dados recentes e gera novas recomendações
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            style={selectStyle}
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
            disabled={running}
          >
            <option value="all">Todos os agentes</option>
            <option value="pricing">Agente de preços</option>
            <option value="ml_position">Agente de posição ML</option>
            <option value="promotions">Agente de promoções</option>
          </select>
          <button
            onClick={handleRun}
            disabled={running}
            style={{
              background: running ? '#a0aec0' : '#2b6cb0',
              color: '#fff', border: 'none', borderRadius: 8,
              padding: '8px 18px', fontSize: 13, fontWeight: 600,
              cursor: running ? 'not-allowed' : 'pointer',
              transition: 'background 0.15s',
            }}
          >
            {running ? '⏳ Executando…' : '▶ Executar'}
          </button>
        </div>
      </div>

      {error && <ErrorBanner msg={error} />}

      {results !== null && (
        <div>
          {results.length === 0 ? (
            <div style={{ color: '#718096', fontSize: 13, padding: '8px 0' }}>
              ✓ Agentes concluíram — nenhuma nova recomendação gerada.
            </div>
          ) : (
            <>
              <div style={{ fontSize: 13, color: '#2d3748', fontWeight: 600, marginBottom: 8 }}>
                {results.length} nova{results.length !== 1 ? 's' : ''} recomendação{results.length !== 1 ? 'ões' : ''} gerada{results.length !== 1 ? 's' : ''}:
              </div>
              {results.map((r, i) => <AgentResultItem key={i} result={r} />)}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Data pills for recommendation cards ───────────────────────────────────────

function DataPills({ data }) {
  const items = []
  if (data.material)                 items.push({ label: 'Material',       value: data.material,              bg: '#edf2f7', color: '#4a5568' })
  if (data.market_min !== undefined) items.push({ label: 'Mín. mercado',   value: fmtBRL(data.market_min),    bg: '#ebf8ff', color: '#2b6cb0' })
  if (data.axen_ref_price !== undefined) items.push({ label: 'AXEN ref.',  value: fmtBRL(data.axen_ref_price),bg: '#e9d8fd', color: '#553c9a' })
  if (data.gap_pct !== undefined)    items.push({ label: 'Gap',            value: fmtPct(data.gap_pct),       bg: '#f0fff4', color: '#276749' })
  if (data.avg_position !== undefined) items.push({ label: 'Pos. média',   value: `#${data.avg_position}`,   bg: '#fefcbf', color: '#744210' })
  if (data.price_changes_count !== undefined) items.push({ label: 'Variações 7d', value: data.price_changes_count, bg: '#fefcbf', color: '#744210' })
  if (!items.length) return null
  return (
    <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
      {items.map(({ label, value, bg, color }) => (
        <span key={label} style={{ background: bg, color, fontSize: 12, padding: '3px 10px', borderRadius: 12 }}>
          {label}: <strong>{value}</strong>
        </span>
      ))}
    </div>
  )
}

// ── Recommendation card ───────────────────────────────────────────────────────

function RecCard({ rec, actionLoading, onDismiss, onApply }) {
  const p   = priorityMeta(rec.priority)
  const tc  = typeColors(rec.type)
  const busy = actionLoading === rec.id
  const data = rec.data_json || {}

  return (
    <div style={{
      border: '1px solid #e2e8f0',
      borderLeft: `4px solid ${p.color}`,
      borderRadius: 8,
      padding: '14px 16px',
      background: '#fafafa',
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1 }}>
          {/* Badges */}
          <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
            <Pill bg={p.bg} color={p.color}>
              {p.text} prioridade
            </Pill>
            <Pill bg={tc.bg} color={tc.color}>
              {typeLabel(rec.type)}
            </Pill>
            {rec.created_at && (
              <span style={{ fontSize: 11, color: '#a0aec0', alignSelf: 'center' }}>
                {fmtDate(rec.created_at)}
              </span>
            )}
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, color: '#1a202c', marginBottom: 4 }}>
            {rec.title}
          </div>
          <div style={{ fontSize: 13, color: '#4a5568', lineHeight: 1.5 }}>
            {rec.body}
          </div>
          <DataPills data={data} />
        </div>

        {/* Actions */}
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
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

const PRIORITY_ORDER = { high: 0, medium: 1, low: 2 }

export default function Recomendacoes() {
  const [recs, setRecs]               = useState([])
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState(null)
  const [actionLoading, setAction]    = useState(null)
  const [filterType, setFilterType]   = useState('')
  const [filterPriority, setFilterPriority] = useState('')
  const [showDismissed, setShowDismissed]   = useState(false)

  // ── Fetch ────────────────────────────────────────────────────────────────

  const fetchRecs = useCallback(() => {
    setLoading(true); setError(null)
    const params = { limit: 200 }
    if (!showDismissed) params.active_only = true
    if (filterType)     params.type        = filterType
    if (filterPriority) params.priority    = filterPriority
    getRecommendations(params)
      .then(setRecs)
      .catch(() => setError('Não foi possível carregar as recomendações.'))
      .finally(() => setLoading(false))
  }, [filterType, filterPriority, showDismissed])

  useEffect(() => { fetchRecs() }, [fetchRecs])

  // ── Actions ──────────────────────────────────────────────────────────────

  const handleDismiss = useCallback(async (id) => {
    setAction(id)
    try   { await dismissRecommendation(id); fetchRecs() }
    catch { /* user can retry */ }
    finally { setAction(null) }
  }, [fetchRecs])

  const handleApply = useCallback(async (id) => {
    setAction(id)
    try   { await applyRecommendation(id); fetchRecs() }
    catch { /* user can retry */ }
    finally { setAction(null) }
  }, [fetchRecs])

  // ── Derived ──────────────────────────────────────────────────────────────

  const sorted = [...recs].sort((a, b) =>
    (PRIORITY_ORDER[a.priority] ?? 99) - (PRIORITY_ORDER[b.priority] ?? 99)
  )

  const countByPriority = {
    high:   recs.filter((r) => r.priority === 'high').length,
    medium: recs.filter((r) => r.priority === 'medium').length,
    low:    recs.filter((r) => r.priority !== 'high' && r.priority !== 'medium').length,
  }

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: '#1a202c', marginBottom: 4 }}>
        Recomendações
      </h1>
      <p style={{ color: '#718096', fontSize: 14, marginBottom: 28 }}>
        Sugestões geradas pelos agentes de inteligência — aplique ou dispense cada item
      </p>

      {/* ── Agent runner ── */}
      <RunAgentsPanel onDone={fetchRecs} />

      {/* ── Filters + summary ── */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap', alignItems: 'center' }}>
        <select style={selectStyle} value={filterType} onChange={(e) => setFilterType(e.target.value)}>
          <option value="">Todos os tipos</option>
          <option value="price_suggestion">Preço</option>
          <option value="ml_position_drop">Posição ML</option>
          <option value="promo_suggestion">Promoção</option>
        </select>
        <select style={selectStyle} value={filterPriority} onChange={(e) => setFilterPriority(e.target.value)}>
          <option value="">Todas as prioridades</option>
          <option value="high">Alta</option>
          <option value="medium">Média</option>
          <option value="low">Baixa</option>
        </select>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#4a5568', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={showDismissed}
            onChange={(e) => setShowDismissed(e.target.checked)}
            style={{ cursor: 'pointer' }}
          />
          Incluir dispensadas
        </label>
        {(filterType || filterPriority) && (
          <button
            onClick={() => { setFilterType(''); setFilterPriority('') }}
            style={{ ...selectStyle, background: '#edf2f7', border: '1px solid #cbd5e0', color: '#4a5568' }}
          >
            ✕ Limpar filtros
          </button>
        )}

        {/* Priority summary pills */}
        {!loading && recs.length > 0 && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
            {countByPriority.high > 0 && (
              <Pill bg="#fff5f5" color="#c53030">{countByPriority.high} alta{countByPriority.high !== 1 ? 's' : ''}</Pill>
            )}
            {countByPriority.medium > 0 && (
              <Pill bg="#fffaf0" color="#c05621">{countByPriority.medium} média{countByPriority.medium !== 1 ? 's' : ''}</Pill>
            )}
            {countByPriority.low > 0 && (
              <Pill bg="#f0fff4" color="#276749">{countByPriority.low} baix{countByPriority.low !== 1 ? 'as' : 'a'}</Pill>
            )}
          </div>
        )}
      </div>

      {error && <ErrorBanner msg={error} />}

      {/* ── Recommendation list ── */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748' }}>
            {showDismissed ? 'Todas as recomendações' : 'Recomendações ativas'}
          </h2>
          <button
            onClick={fetchRecs}
            disabled={loading}
            style={{ fontSize: 12, color: '#3182ce', background: 'none', border: 'none', cursor: 'pointer', padding: '4px 8px' }}
          >
            ↻ Atualizar
          </button>
        </div>

        {loading ? (
          <EmptyState msg="Carregando…" />
        ) : sorted.length === 0 ? (
          <EmptyState msg="Nenhuma recomendação encontrada. Execute os agentes ou ajuste os filtros." />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {sorted.map((rec) => (
              <RecCard
                key={rec.id}
                rec={rec}
                actionLoading={actionLoading}
                onDismiss={handleDismiss}
                onApply={handleApply}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
