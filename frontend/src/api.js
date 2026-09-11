/**
 * api.js — Thin axios wrapper for the AXEN Intelligence API.
 *
 * All requests go to /api/* which Vite proxies to http://localhost:8000
 * in development, and to the same origin in production.
 *
 * Every function returns the response `.data` directly (the parsed JSON body).
 * Errors propagate as-is so callers can handle them with try/catch or
 * React Query's error boundary.
 */

import axios from 'axios'

const http = axios.create({ baseURL: '/api' })

// ── Prices ────────────────────────────────────────────────────────────────────

/**
 * GET /prices/
 * @returns {Promise<Array<{material, market_min, axen_ref, gap_pct}>>}
 */
export const getPrices = () =>
  http.get('/prices/').then((r) => r.data)

/**
 * GET /prices/:material
 * @param {string} material
 * @returns {Promise<{material, market_min, axen_ref, gap_pct}>}
 */
export const getPriceByMaterial = (material) =>
  http.get(`/prices/${material}`).then((r) => r.data)

// ── Competitors ───────────────────────────────────────────────────────────────

/**
 * GET /competitors/
 * @param {{ material?: string, store?: string }} [params]
 * @returns {Promise<Array<{store, name, price, material, url, delivery_info, discount_pct, scraped_at}>>}
 */
export const getCompetitors = (params = {}) =>
  http.get('/competitors/', { params }).then((r) => r.data)

/**
 * GET /competitors/history
 * @param {{ material?: string, days?: number }} [params]
 * @returns {Promise<Array<{store, material, price_before, price_after, change_pct, changed_at}>>}
 */
export const getPriceHistory = (params = {}) =>
  http.get('/competitors/history', { params }).then((r) => r.data)

// ── Recommendations ───────────────────────────────────────────────────────────

/**
 * GET /recommendations/
 * @param {{ type?: string, priority?: string, active_only?: boolean, limit?: number }} [params]
 * @returns {Promise<Array>}
 */
export const getRecommendations = (params = {}) =>
  http.get('/recommendations/', { params }).then((r) => r.data)

/**
 * POST /recommendations/:id/dismiss
 * @param {number} id
 * @returns {Promise<{ok: boolean}>}
 */
export const dismissRecommendation = (id) =>
  http.post(`/recommendations/${id}/dismiss`).then((r) => r.data)

/**
 * POST /recommendations/:id/apply
 * @param {number} id
 * @returns {Promise<{ok: boolean}>}
 */
export const applyRecommendation = (id) =>
  http.post(`/recommendations/${id}/apply`).then((r) => r.data)

// ── Agents ────────────────────────────────────────────────────────────────────

/**
 * GET /agents/
 * @returns {Promise<Array<{name, description}>>}
 */
export const getAgents = () =>
  http.get('/agents/').then((r) => r.data)

/**
 * POST /agents/run
 * @param {"pricing"|"ml_position"|"promotions"|"all"} [agent="all"]
 * @returns {Promise<Array>}
 */
export const runAgents = (agent = 'all') =>
  http.post('/agents/run', { agent }).then((r) => r.data)

// ── Integrations ──────────────────────────────────────────────────────────────

/**
 * GET /integrations/
 * @returns {Promise<Array<{name, enabled}>>}
 */
export const getIntegrations = () =>
  http.get('/integrations/').then((r) => r.data)

/**
 * GET /integrations/mercadolivre/sales
 * @param {{ days?: number }} [params]
 * @returns {Promise<Array>}
 */
export const getMLSales = (params = {}) =>
  http.get('/integrations/mercadolivre/sales', { params }).then((r) => r.data)

// ── Sales ─────────────────────────────────────────────────────────────────────

/**
 * GET /sales/
 * @param {{ days?: number, platform?: string }} [params]
 * @returns {Promise<{top_products: Array, weekly: Array}>}
 */
export const getSales = (params = {}) =>
  http.get('/sales/', { params }).then((r) => r.data)

/**
 * GET /sales/hoje
 * @returns {Promise<{date: string, platforms: object, total: object}>}
 */
export const getSalesToday = () =>
  http.get('/sales/hoje').then((r) => r.data)

/**
 * GET /sales/by-state
 * @param {{ days?: number, material?: string }} [params]
 * @returns {Promise<Array>}
 */
export const getSalesByState = (params = {}) =>
  http.get('/sales/by-state', { params }).then((r) => r.data)

// ── ROAS ──────────────────────────────────────────────────────────────────────

/**
 * GET /roas/
 * @param {{ days?: number }} [params]
 * @returns {Promise<{avg_roas, total_spend, total_revenue, campaign_count, best_campaign}>}
 */
export const getRoas = (params = {}) =>
  http.get('/roas/', { params }).then((r) => r.data)

/**
 * GET /roas/campaigns
 * @param {{ days?: number }} [params]
 * @returns {Promise<Array>}
 */
export const getRoasCampaigns = (params = {}) =>
  http.get('/roas/campaigns', { params }).then((r) => r.data)

// ── Sync ──────────────────────────────────────────────────────────────────────

/**
 * POST /sync/run
 * Dispara scraper + agentes + vendas ML em background.
 * @returns {Promise<{started: boolean, message: string}>}
 */
export const runSync = () =>
  http.post('/sync/run').then((r) => r.data)

// ── Tarefas ───────────────────────────────────────────────────────────────────

/**
 * GET /tarefas/hoje
 * @returns {Promise<{total: number, tasks: Array, errors: Array}>}
 */
export const getTarefasHoje = () =>
  http.get('/tarefas/hoje').then((r) => r.data)

// ── Visits ────────────────────────────────────────────────────────────────────

/**
 * GET /visits/historico?days=N
 * @param {number} [days=30]
 * @returns {Promise<{days: number, series: Array, summary: object}>}
 */
export const getVisitsHistorico = (days = 30) =>
  http.get('/visits/historico', { params: { days } }).then((r) => r.data)

export const getVisitsPorModelo = (days = 30) =>
  http.get('/visits/por-modelo', { params: { days } }).then((r) => r.data)

export const getAnunciosML = (days = 30) =>
  http.get('/visits/anuncios-ml', { params: { days } }).then((r) => r.data)

// ── Sync ──────────────────────────────────────────────────────────────────────

/**
 * GET /sync/status
 * @returns {Promise<{status: string, step: string|null, started_at: string|null, finished_at: string|null, result: object|null, error: string|null}>}
 */
export const getSyncStatus = () =>
  http.get('/sync/status').then((r) => r.data)
