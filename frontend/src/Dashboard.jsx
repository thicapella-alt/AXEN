import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  Cell, ReferenceLine, Legend,
} from 'recharts'
import { getPrices, getRecommendations, getCompetitors, runSync, getSyncStatus } from './api.js'

// ── Palette ───────────────────────────────────────────────────────────────────

const C = {
  high:   '#e53e3e',
  medium: '#dd6b20',
  ok:     '#38a169',
  card:   '#ffffff',
  axen:   '#2b6cb0',
  market: '#a0aec0',
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function avg(arr) {
  const valid = arr.filter((v) => v !== null && v !== undefined && !isNaN(v))
  return valid.length ? valid.reduce((s, v) => s + v, 0) / valid.length : null
}

function fmtBRL(v) {
  if (v === null || v === undefined) return '—'
  return `R$ ${Number(v).toFixed(2).replace('.', ',')}`
}

function fmtPct(v, dec = 1) {
  if (v === null || v === undefined) return '—'
  return `${Number(v).toFixed(dec)}%`
}

function fmtDateTime(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return isNaN(d) ? null : d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function gapColor(gap) {
  if (gap === null || gap === undefined) return C.market
  if (gap > 25) return C.high
  if (gap > 10) return C.medium
  return C.ok
}

const STEP_LABELS = {
  scraper:            '⚙️ Coletando preços dos concorrentes…',
  agentes:            '🤖 Analisando dados com agentes…',
  vendas_ml:          '🛒 Atualizando vendas do Mercado Livre…',
  vendas_nuvemshop:   '☁️ Atualizando vendas da Nuvemshop…',
  visitas_ml:         '📊 Coletando visitas do Mercado Livre…',
  visitas_nuvemshop:  '📈 Coletando visitas da Nuvemshop…',
}

// ── Shared UI ─────────────────────────────────────────────────────────────────

const card = {
  background: C.card, borderRadius: 12,
  padding: '20px 24px', boxShadow: '0 1px 4px #0001',
}

const thS = {
  textAlign: 'left', padding: '10px 12px', fontSize: 12, fontWeight: 600,
  color: '#718096', textTransform: 'uppercase', letterSpacing: 0.6,
  borderBottom: '1px solid #e2e8f0', whiteSpace: 'nowrap',
}

const tdS = { padding: '10px 12px', fontSize: 13, color: '#2d3748', borderBottom: '1px solid #f0f0f0' }

function SummaryCard({ label, value, sub, valueColor }) {
  return (
    <div style={{ ...card, minWidth: 180, flex: 1 }}>
      <div style={{ fontSize: 12, color: '#718096', textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, color: valueColor || '#1a202c' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: '#a0aec0', marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

function SectionTitle({ children, sub }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <h2 style={{ fontSize: 16, fontWeight: 600, color: '#2d3748', marginBottom: 2 }}>{children}</h2>
      {sub && <p style={{ fontSize: 12, color: '#a0aec0' }}>{sub}</p>}
    </div>
  )
}

// ── Botão Sincronizar ─────────────────────────────────────────────────────────

function SyncButton({ onSyncDone }) {
  const [st, setSt] = useState({ status: 'idle', step: null, finished_at: null, result: null, error: null })
  const pollRef = useRef(null)

  const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }

  const startPoll = useCallback(() => {
    stopPoll()
    pollRef.current = setInterval(async () => {
      try {
        const s = await getSyncStatus()
        setSt(s)
        if (s.status !== 'running') { stopPoll(); if (s.status === 'done') onSyncDone() }
      } catch { stopPoll() }
    }, 3000)
  }, [onSyncDone])

  useEffect(() => { getSyncStatus().then(setSt).catch(() => {}); return stopPoll }, [])

  const handleSync = async () => {
    try {
      await runSync()
      setSt((s) => ({ ...s, status: 'running' }))
      startPoll()
    } catch {
      setSt((s) => ({ ...s, status: 'error', error: 'Falha ao iniciar sincronização.' }))
    }
  }

  const running  = st.status === 'running'
  const lastSync = fmtDateTime(st.finished_at)
  const step     = running ? (STEP_LABELS[st.step] || '⏳ Sincronizando…') : null
  const summary  = st.result && st.status === 'done'
    ? `${st.result.scraper?.products ?? 0} produtos · ${st.result.agentes?.recommendations ?? 0} recomendações · ${st.result.vendas_ml?.orders ?? 0} pedidos ML`
    : null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
      <button onClick={handleSync} disabled={running} style={{
        background: running ? '#a0aec0' : C.axen, color: '#fff', border: 'none',
        borderRadius: 8, padding: '10px 20px', fontSize: 13, fontWeight: 700,
        cursor: running ? 'not-allowed' : 'pointer', whiteSpace: 'nowrap',
      }}>
        {running ? '⏳ Sincronizando…' : '🔄 Sincronizar'}
      </button>
      {step    && <div style={{ fontSize: 12, color: '#718096', textAlign: 'right' }}>{step}</div>}
      {!running && lastSync && <div style={{ fontSize: 11, color: '#a0aec0', textAlign: 'right' }}>Última sync: {lastSync}</div>}
      {summary && <div style={{ fontSize: 11, color: '#276749', textAlign: 'right' }}>✓ {summary}</div>}
      {st.status === 'error' && <div style={{ fontSize: 11, color: '#c53030', textAlign: 'right' }}>⚠️ {st.error || 'Erro na sincronização'}</div>}
    </div>
  )
}

// ── Tabela: preço médio por competidor + material ─────────────────────────────

function CompetitorTable({ competitors, axenRef, loading }) {
  // Agrupa por loja → material, calcula média e mínimo
  const grouped = useMemo(() => {
    const map = {}
    for (const c of competitors) {
      if (!c.store || !c.material || !c.price) continue
      if (!map[c.store]) map[c.store] = {}
      if (!map[c.store][c.material]) map[c.store][c.material] = []
      map[c.store][c.material].push(c.price)
    }

    // Converte em array de lojas, cada uma com seus materiais
    return Object.entries(map)
      .map(([store, materials]) => {
        const matRows = Object.entries(materials).map(([material, prices]) => {
          const minPrice = Math.min(...prices)
          const avgPrice = avg(prices)
          const ref      = axenRef[material] ?? null
          const gap      = ref !== null ? (ref - minPrice) / minPrice * 100 : null
          return { material, avgPrice, minPrice, gap }
        }).sort((a, b) => a.material.localeCompare(b.material))

        const worstGap = Math.max(...matRows.map((r) => r.gap ?? -Infinity))
        return { store, matRows, worstGap }
      })
      .sort((a, b) => b.worstGap - a.worstGap)
  }, [competitors, axenRef])

  if (loading) return <div style={{ color: '#a0aec0', padding: 32, textAlign: 'center' }}>Carregando…</div>
  if (!grouped.length) return <div style={{ color: '#a0aec0', padding: 32, textAlign: 'center' }}>Nenhum dado. Clique em Sincronizar.</div>

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            {['Loja', 'Material', 'Preço médio', 'Preço mínimo', 'Gap vs. AXEN'].map((h) => (
              <th key={h} style={thS}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grouped.map(({ store, matRows }) =>
            matRows.map((r, i) => {
              const gc = gapColor(r.gap)
              const isFirst = i === 0
              return (
                <tr key={`${store}-${r.material}`} style={{ background: isFirst ? '#f7fafc' : undefined }}>
                  {/* Célula da loja: só aparece na primeira linha do grupo */}
                  <td style={{
                    ...tdS,
                    fontWeight: 600,
                    color: '#1a202c',
                    borderTop: isFirst ? '2px solid #e2e8f0' : undefined,
                    verticalAlign: 'top',
                    visibility: isFirst ? 'visible' : 'hidden',
                  }}>
                    {isFirst ? store : ''}
                  </td>
                  <td style={{ ...tdS, textTransform: 'capitalize', borderTop: isFirst ? '2px solid #e2e8f0' : undefined }}>
                    {r.material}
                  </td>
                  <td style={{ ...tdS, borderTop: isFirst ? '2px solid #e2e8f0' : undefined }}>
                    {fmtBRL(r.avgPrice)}
                  </td>
                  <td style={{ ...tdS, fontWeight: 600, borderTop: isFirst ? '2px solid #e2e8f0' : undefined }}>
                    {fmtBRL(r.minPrice)}
                  </td>
                  <td style={{ ...tdS, fontWeight: 700, color: gc, borderTop: isFirst ? '2px solid #e2e8f0' : undefined }}>
                    {r.gap !== null ? fmtPct(r.gap) : '—'}
                  </td>
                </tr>
              )
            })
          )}
        </tbody>
      </table>
      <div style={{ display: 'flex', gap: 16, marginTop: 12, fontSize: 12, color: '#718096' }}>
        {[
          { color: C.high,   label: '> 25% — alta prioridade' },
          { color: C.medium, label: '10–25% — média prioridade' },
          { color: C.ok,     label: '< 10% — dentro do limite' },
        ].map(({ color, label }) => (
          <span key={label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: color, display: 'inline-block' }} />
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}

// ── Gráfico: mercado vs. AXEN por material ────────────────────────────────────

function MaterialTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  return (
    <div style={{ background: '#fff', border: '1px solid #e2e8f0', borderRadius: 8, padding: '10px 14px', fontSize: 13 }}>
      <strong style={{ textTransform: 'capitalize' }}>{label}</strong>
      <div>Mínimo mercado: {fmtBRL(d?.marketMin)}</div>
      <div>Média mercado:  {fmtBRL(d?.marketAvg)}</div>
      <div>Máximo mercado: {fmtBRL(d?.marketMax)}</div>
      <div>Referência AXEN: {fmtBRL(d?.axenRef)}</div>
    </div>
  )
}

function MaterialChart({ competitors, axenRef, loading }) {
  const data = useMemo(() => {
    const map = {}
    for (const c of competitors) {
      if (!c.material || !c.price) continue
      if (!map[c.material]) map[c.material] = []
      map[c.material].push(c.price)
    }
    return Object.entries(map).map(([material, prices]) => ({
      material,
      marketMin: Math.min(...prices),
      marketAvg: avg(prices),
      marketMax: Math.max(...prices),
      axenRef:   axenRef[material] ?? null,
    })).sort((a, b) => a.material.localeCompare(b.material))
  }, [competitors, axenRef])

  if (loading) return <div style={{ color: '#a0aec0', padding: 32, textAlign: 'center' }}>Carregando…</div>
  if (!data.length) return <div style={{ color: '#a0aec0', padding: 32, textAlign: 'center' }}>Nenhum dado. Clique em Sincronizar.</div>

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <XAxis dataKey="material" tick={{ fontSize: 13, textTransform: 'capitalize' }} axisLine={false} tickLine={false} />
        <YAxis tickFormatter={(v) => `R$${v}`} tick={{ fontSize: 11 }} axisLine={false} tickLine={false} width={64} />
        <Tooltip content={<MaterialTooltip />} cursor={{ fill: '#f7fafc' }} />
        <Legend wrapperStyle={{ fontSize: 12, paddingTop: 8 }} />
        <Bar dataKey="marketMin" name="Mínimo mercado" fill="#a0aec0" radius={[4, 4, 0, 0]} maxBarSize={32} />
        <Bar dataKey="marketAvg" name="Média mercado"  fill="#718096" radius={[4, 4, 0, 0]} maxBarSize={32} />
        <Bar dataKey="axenRef"   name="AXEN ref."      fill={C.axen}  radius={[4, 4, 0, 0]} maxBarSize={32} />
      </BarChart>
    </ResponsiveContainer>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [prices,          setPrices]          = useState([])
  const [recommendations, setRecommendations] = useState([])
  const [competitors,     setCompetitors]     = useState([])
  const [loadingPrices,   setLoadingPrices]   = useState(true)
  const [loadingRecs,     setLoadingRecs]     = useState(true)
  const [loadingComp,     setLoadingComp]     = useState(true)
  const [error,           setError]           = useState(null)

  const fetchAll = useCallback(() => {
    setLoadingPrices(true)
    getPrices().then(setPrices).catch(setError).finally(() => setLoadingPrices(false))

    setLoadingRecs(true)
    getRecommendations({ active_only: true, limit: 1000 })
      .then(setRecommendations).catch(setError).finally(() => setLoadingRecs(false))

    setLoadingComp(true)
    getCompetitors().then(setCompetitors).catch(setError).finally(() => setLoadingComp(false))
  }, [])

  useEffect(() => { fetchAll() }, [fetchAll])

  // ── Derived ──────────────────────────────────────────────────────────────

  const axenRef = useMemo(() => {
    const m = {}
    for (const p of prices) m[p.material] = p.axen_ref
    return m
  }, [prices])

  const pricesWithGap = prices.filter((p) => p.gap_pct !== null)
  const avgMarketMin  = avg(prices.map((p) => p.market_min))
  const avgGap        = avg(pricesWithGap.map((p) => p.gap_pct))
  const highAlerts    = recommendations.filter((r) => r.priority === 'high').length
  const mlRecs        = recommendations.filter((r) => r.type === 'ml_position_drop')
  const mlAvgPos      = mlRecs.length ? avg(mlRecs.map((r) => r.data_json?.avg_position).filter(Boolean)) : null

  const isLoading = loadingPrices || loadingRecs || loadingComp

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 28 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: '#1a202c', marginBottom: 4 }}>Acompanhamento de Preços</h1>
          <p style={{ color: '#718096', fontSize: 14 }}>Visão geral da inteligência comercial AXEN</p>
        </div>
        <SyncButton onSyncDone={fetchAll} />
      </div>

      {error && (
        <div style={{ background: '#fff5f5', border: '1px solid #feb2b2', borderRadius: 8, padding: '12px 16px', marginBottom: 24, color: '#c53030', fontSize: 14 }}>
          ⚠️ Não foi possível carregar alguns dados. Verifique se o servidor está rodando.
        </div>
      )}

      {/* Summary cards */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 32 }}>
        <SummaryCard
          label="Preço médio de mercado"
          value={isLoading ? '…' : fmtBRL(avgMarketMin)}
          sub="menor preço por material (média)"
        />
        <SummaryCard
          label="Gap médio AXEN vs mercado"
          value={isLoading ? '…' : (avgGap !== null ? fmtPct(avgGap) : '—')}
          sub="AXEN acima do piso competitivo"
          valueColor={avgGap === null ? undefined : avgGap > 25 ? C.high : avgGap > 10 ? C.medium : C.ok}
        />
        <SummaryCard
          label="Alertas ativos"
          value={isLoading ? '…' : recommendations.length}
          sub={highAlerts > 0 ? `${highAlerts} de alta prioridade` : 'nenhuma alta prioridade'}
          valueColor={highAlerts > 0 ? C.high : undefined}
        />
        <SummaryCard
          label="Posição ML média"
          value={isLoading ? '…' : (mlAvgPos !== null ? mlAvgPos.toFixed(1) : '—')}
          sub={mlRecs.length ? `${mlRecs.length} itens com instabilidade` : 'sem dados de posição'}
          valueColor={mlAvgPos !== null && mlAvgPos >= 20 ? C.high : undefined}
        />
      </div>

      {/* Tabela por competidor */}
      <div style={{ ...card, marginBottom: 24 }}>
        <SectionTitle sub="Preço médio e mínimo de cada loja por material, com gap em relação à referência AXEN">
          Preços por concorrente
        </SectionTitle>
        <CompetitorTable competitors={competitors} axenRef={axenRef} loading={loadingComp} />
      </div>

      {/* Gráfico por material */}
      <div style={{ ...card, marginBottom: 24 }}>
        <SectionTitle sub="Comparativo entre piso de mercado, média de mercado e referência AXEN por material">
          Mercado vs. AXEN por material
        </SectionTitle>
        <MaterialChart competitors={competitors} axenRef={axenRef} loading={loadingComp} />
      </div>
    </div>
  )
}
