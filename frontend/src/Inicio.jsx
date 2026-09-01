import { useEffect, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { getSalesToday, getVisitsHistorico, getVisitsPorModelo, getTarefasHoje } from './api.js'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtBRL(v) {
  if (v === null || v === undefined) return '—'
  return `R$ ${Number(v).toFixed(2).replace('.', ',')}`
}

function fmtDate(iso) {
  if (!iso) return ''
  const [y, m, d] = iso.split('-')
  return `${d}/${m}/${y}`
}

function fmtShortDate(iso) {
  if (!iso) return ''
  const [, m, d] = iso.split('-')
  return `${d}/${m}`
}

// ── Styles ────────────────────────────────────────────────────────────────────

const card = {
  background: '#fff',
  borderRadius: 12,
  padding: '24px 28px',
  boxShadow: '0 1px 4px #0001',
  flex: 1,
  minWidth: 220,
}

const PLATFORM_COLOR = {
  mercadolivre: '#f5a623',
  nuvemshop:    '#6c47ff',
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Metric({ label, value, highlight }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: '#718096', textTransform: 'uppercase', letterSpacing: 0.7, marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 700, color: highlight ? '#276749' : '#1a202c' }}>
        {value}
      </div>
    </div>
  )
}

function PlatformCard({ name, icon, color, data }) {
  const orders    = data?.orders    ?? 0
  const revenue   = data?.revenue   ?? 0
  const avgTicket = data?.avg_ticket ?? 0

  return (
    <div style={{ ...card, borderTop: `4px solid ${color}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
        <span style={{ fontSize: 22 }}>{icon}</span>
        <span style={{ fontSize: 15, fontWeight: 700, color: '#1a202c' }}>{name}</span>
        {orders === 0 && (
          <span style={{
            marginLeft: 'auto', fontSize: 11, background: '#fff5f5',
            color: '#c53030', padding: '2px 8px', borderRadius: 99, fontWeight: 600,
          }}>Sem vendas hoje</span>
        )}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 16 }}>
        <Metric label="Pedidos" value={orders} />
        <Metric label="Receita" value={fmtBRL(revenue)} highlight={revenue > 0} />
        <Metric label="Ticket médio" value={fmtBRL(avgTicket)} />
      </div>
    </div>
  )
}

function TotalBar({ total }) {
  if (!total || total.orders === 0) return null
  return (
    <div style={{
      background: '#ebf8ff', border: '1px solid #bee3f8',
      borderRadius: 10, padding: '14px 20px',
      display: 'flex', gap: 32, alignItems: 'center', marginBottom: 24,
    }}>
      <span style={{ fontSize: 13, color: '#2c5282', fontWeight: 600 }}>Total hoje</span>
      <span style={{ fontSize: 18, fontWeight: 700, color: '#2b6cb0' }}>
        {total.orders} pedido{total.orders !== 1 ? 's' : ''}
      </span>
      <span style={{ fontSize: 18, fontWeight: 700, color: '#276749' }}>
        {fmtBRL(total.revenue)}
      </span>
    </div>
  )
}

function VisitSummaryCard({ platform, icon, name, color, summary }) {
  const s     = summary?.[platform]
  const total = s?.total_visits ?? 0
  const today = s?.today ?? 0
  const conv  = s?.conversion_rate

  return (
    <div style={{ ...card, borderTop: `4px solid ${color}`, minWidth: 180 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <span style={{ fontSize: 18 }}>{icon}</span>
        <span style={{ fontSize: 14, fontWeight: 700, color: '#1a202c' }}>{name}</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: conv !== undefined ? '1fr 1fr 1fr' : '1fr 1fr', gap: 14 }}>
        <Metric label="Visitas hoje"  value={today.toLocaleString('pt-BR')} highlight={today > 0} />
        <Metric label="Total 30d"     value={total.toLocaleString('pt-BR')} />
        {conv !== undefined && <Metric label="Conversão" value={`${conv}%`} />}
      </div>
    </div>
  )
}

function PeriodSelector({ value, onChange }) {
  return (
    <div style={{ display: 'flex', gap: 6 }}>
      {[7, 30, 90].map(d => (
        <button
          key={d}
          onClick={() => onChange(d)}
          style={{
            padding: '4px 12px', borderRadius: 99, fontSize: 12, fontWeight: 600,
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

// ── Tarefas ───────────────────────────────────────────────────────────────────

const TASK_META = {
  pedido_envio: { icon: '📦', label: 'Envio pendente',     color: '#e53e3e' },
  pergunta_ml:  { icon: '💬', label: 'Pergunta sem resp.', color: '#dd6b20' },
  roas_critico: { icon: '📉', label: 'ROAS crítico',       color: '#e53e3e' },
}

function TarefaCard({ task, onDismiss }) {
  const meta = TASK_META[task.type] ?? { icon: '⚠️', label: task.type, color: '#718096' }

  return (
    <div style={{
      background: '#fff', borderRadius: 10,
      boxShadow: '0 1px 3px #0001', marginBottom: 10,
      display: 'flex', alignItems: 'stretch', overflow: 'hidden',
    }}>
      {/* Priority stripe */}
      <div style={{ width: 4, background: meta.color, flexShrink: 0 }} />

      <div style={{ padding: '14px 16px', flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <span style={{ fontSize: 16 }}>{meta.icon}</span>
          <span style={{
            fontSize: 11, fontWeight: 600, color: meta.color,
            textTransform: 'uppercase', letterSpacing: 0.5,
          }}>{meta.label}</span>
          <span style={{
            marginLeft: 'auto', fontSize: 11, color: '#a0aec0',
            background: '#f7fafc', padding: '1px 7px', borderRadius: 99,
          }}>
            {task.platform === 'mercadolivre' ? '🛒 ML' : '☁️ Nuvemshop'}
          </span>
        </div>
        <div style={{ fontSize: 14, fontWeight: 600, color: '#1a202c', marginBottom: 2 }}>
          {task.title}
        </div>
        <div style={{ fontSize: 12, color: '#718096' }}>{task.description}</div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '12px 14px', justifyContent: 'center' }}>
        {task.action_url && (
          <a
            href={task.action_url}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              fontSize: 12, fontWeight: 600, color: '#2b6cb0',
              background: '#ebf8ff', border: '1px solid #bee3f8',
              borderRadius: 6, padding: '5px 12px', textDecoration: 'none',
              whiteSpace: 'nowrap',
            }}
          >
            Abrir ↗
          </a>
        )}
        <button
          onClick={() => onDismiss(task.id)}
          style={{
            fontSize: 12, color: '#a0aec0', background: 'none',
            border: '1px solid #e2e8f0', borderRadius: 6,
            padding: '5px 12px', cursor: 'pointer', whiteSpace: 'nowrap',
          }}
        >
          Dispensar
        </button>
      </div>
    </div>
  )
}

function TarefasSection({ tasks, loading, onDismiss }) {
  if (loading) return (
    <div style={{ color: '#a0aec0', padding: '16px 0', fontSize: 13 }}>Carregando tarefas…</div>
  )

  if (tasks.length === 0) return (
    <div style={{
      background: '#f0fff4', border: '1px solid #9ae6b4',
      borderRadius: 8, padding: '12px 16px', fontSize: 13, color: '#276749',
    }}>
      ✅ Nenhuma tarefa pendente — tudo em dia!
    </div>
  )

  return <div>{tasks.map(t => <TarefaCard key={t.id} task={t} onDismiss={onDismiss} />)}</div>
}

const PLATFORM_LABEL = { mercadolivre: 'Mercado Livre', nuvemshop: 'Nuvemshop' }
const PLATFORM_ICON  = { mercadolivre: '🛒', nuvemshop: '☁️' }

function VisitsChart({ series, days }) {
  if (!series || series.length === 0) {
    return (
      <div style={{ color: '#a0aec0', fontSize: 13, padding: '24px 0', textAlign: 'center' }}>
        Nenhum dado de visitas ainda — sincronize para coletar.
      </div>
    )
  }

  // Merge todas as datas das plataformas em dataset único
  const dateMap = {}
  for (const s of series) {
    for (const d of s.data) {
      if (!dateMap[d.date]) dateMap[d.date] = { date: d.date }
      dateMap[d.date][s.platform] = d.visits
    }
  }
  const chartData = Object.values(dateMap).sort((a, b) => a.date.localeCompare(b.date))
  const interval  = days <= 7 ? 0 : Math.max(0, Math.floor(chartData.length / 7) - 1)

  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis
          dataKey="date"
          tickFormatter={fmtShortDate}
          tick={{ fontSize: 11 }}
          interval={interval}
        />
        <YAxis tick={{ fontSize: 11 }} width={42} />
        <Tooltip
          formatter={(v, name) => [v?.toLocaleString('pt-BR'), PLATFORM_LABEL[name] ?? name]}
          labelFormatter={fmtDate}
        />
        <Legend formatter={name => `${PLATFORM_ICON[name] ?? ''} ${PLATFORM_LABEL[name] ?? name}`} />
        {series.map(s => (
          <Line
            key={s.platform}
            type="monotone"
            dataKey={s.platform}
            stroke={PLATFORM_COLOR[s.platform] ?? '#8884d8'}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

function MLModelTable({ modelos, loading }) {
  if (loading) return (
    <div style={{ color: '#a0aec0', fontSize: 13, padding: '12px 0' }}>Carregando…</div>
  )
  if (!modelos || modelos.length === 0) return null

  const max = modelos[0]?.visits || 1

  return (
    <div style={{ marginTop: 24 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#2d3748', marginBottom: 10 }}>
        🛒 Visitas por modelo — Mercado Livre
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {modelos.map(m => (
          <div key={m.item_id} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ fontSize: 12, color: '#4a5568', width: 200, flexShrink: 0,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
              title={m.title}>
              {m.title}
            </div>
            <div style={{ flex: 1, background: '#f7fafc', borderRadius: 99, height: 8, overflow: 'hidden' }}>
              <div style={{
                width: `${Math.round(m.visits / max * 100)}%`,
                height: '100%', background: '#f5a623', borderRadius: 99,
              }} />
            </div>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#1a202c', width: 36, textAlign: 'right' }}>
              {m.visits}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function Inicio() {
  const [salesData,     setSalesData]     = useState(null)
  const [visitsData,    setVisitsData]    = useState(null)
  const [modelosData,   setModelosData]   = useState(null)
  const [visitDays,     setVisitDays]     = useState(30)
  const [tarefas,       setTarefas]       = useState([])
  const [dismissed,     setDismissed]     = useState(new Set())
  const [loadingSales,  setLoadingSales]  = useState(true)
  const [loadingVisits, setLoadingVisits] = useState(true)
  const [loadingModelos, setLoadingModelos] = useState(true)
  const [loadingTarefas, setLoadingTarefas] = useState(true)
  const [salesError,    setSalesError]    = useState(null)

  useEffect(() => {
    getSalesToday()
      .then(setSalesData)
      .catch(setSalesError)
      .finally(() => setLoadingSales(false))
  }, [])

  useEffect(() => {
    setLoadingVisits(true)
    setLoadingModelos(true)
    getVisitsHistorico(visitDays)
      .then(setVisitsData)
      .catch(() => {})
      .finally(() => setLoadingVisits(false))
    getVisitsPorModelo(visitDays)
      .then(d => setModelosData(d.modelos ?? []))
      .catch(() => {})
      .finally(() => setLoadingModelos(false))
  }, [visitDays])

  useEffect(() => {
    getTarefasHoje()
      .then(d => setTarefas(d.tasks ?? []))
      .catch(() => {})
      .finally(() => setLoadingTarefas(false))
  }, [])

  const handleDismiss = (id) => setDismissed(prev => new Set([...prev, id]))

  const platforms    = salesData?.platforms ?? {}
  const ml           = platforms['mercadolivre']
  const nuv          = platforms['nuvemshop']
  const visitSeries  = visitsData?.series  ?? []
  const visitSummary = visitsData?.summary ?? {}
  const visibleTasks = tarefas.filter(t => !dismissed.has(t.id))
  const pendingCount = visibleTasks.length

  return (
    <div>
      {/* Header */}
      <div style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: '#1a202c', marginBottom: 4 }}>
          Início
        </h1>
        <p style={{ color: '#718096', fontSize: 14 }}>
          Resumo do dia{salesData?.date ? ` — ${fmtDate(salesData.date)}` : ''}
        </p>
      </div>

      {/* ── Tarefas do dia ── */}
      <div style={{ marginBottom: 32 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', margin: 0 }}>
            O que fazer agora
          </h2>
          {pendingCount > 0 && (
            <span style={{
              background: '#e53e3e', color: '#fff',
              fontSize: 11, fontWeight: 700,
              borderRadius: 99, padding: '2px 8px',
            }}>
              {pendingCount}
            </span>
          )}
        </div>
        <TarefasSection
          tasks={visibleTasks}
          loading={loadingTarefas}
          onDismiss={handleDismiss}
        />
      </div>

      {salesError && (
        <div style={{
          background: '#fff5f5', border: '1px solid #feb2b2',
          borderRadius: 8, padding: '12px 16px', marginBottom: 24,
          color: '#c53030', fontSize: 14,
        }}>
          ⚠️ Não foi possível carregar os dados. Verifique se o servidor está rodando.
        </div>
      )}

      {/* ── Vendas ── */}
      <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', marginBottom: 12 }}>
        Vendas (últimas 48h)
      </h2>

      {loadingSales ? (
        <div style={{ color: '#a0aec0', padding: '20px 0' }}>Carregando vendas…</div>
      ) : (
        <>
          <TotalBar total={salesData?.total} />
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 32 }}>
            <PlatformCard name="Mercado Livre" icon="🛒" color="#ffe600" data={ml} />
            <PlatformCard name="Nuvemshop"     icon="☁️" color="#6c47ff" data={nuv} />
          </div>

          {!ml && !nuv && (
            <div style={{
              background: '#fffff0', border: '1px solid #faf089',
              borderRadius: 8, padding: '14px 18px', fontSize: 14,
              color: '#744210', marginBottom: 32,
            }}>
              💡 Nenhuma venda registrada hoje. Clique em <strong>Sincronizar</strong> na tela
              de <em>Acompanhamento de Preços</em> para importar os pedidos.
            </div>
          )}
        </>
      )}

      {/* ── Visitas ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: '#2d3748', margin: 0 }}>
          Visitas por plataforma
        </h2>
        <PeriodSelector value={visitDays} onChange={setVisitDays} />
      </div>

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 20 }}>
        <VisitSummaryCard
          platform="mercadolivre"
          icon="🛒" name="Mercado Livre" color="#ffe600"
          summary={visitSummary}
        />
        <VisitSummaryCard
          platform="nuvemshop"
          icon="☁️" name="Nuvemshop" color="#6c47ff"
          summary={visitSummary}
        />
      </div>

      <div style={{ ...card, marginBottom: 32, padding: '20px 24px' }}>
        {loadingVisits ? (
          <div style={{ color: '#a0aec0', padding: '24px 0', textAlign: 'center' }}>
            Carregando visitas…
          </div>
        ) : (
          <VisitsChart series={visitSeries} days={visitDays} />
        )}
        <MLModelTable modelos={modelosData} loading={loadingModelos} />
      </div>
    </div>
  )
}
