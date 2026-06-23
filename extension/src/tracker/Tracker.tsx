/**
 * Tracker — page plein écran qui liste toutes les candidatures et permet de
 * patcher leur statut/notes. Ouverte depuis le popup via OPEN_TRACKER ou
 * directement à chrome-extension://<id>/src/tracker/index.html.
 *
 * Appelle directement /applications et /applications/{id} sur le backend
 * (CORS allow_origins="*" côté FastAPI rend l'appel possible sans passer
 * par le service worker).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  APPLICATION_STATUSES,
  STATUS_LABELS,
  isProgressedStatus,
  type ApplicationStatus,
} from '../shared/status'

const BACKEND_URL = 'http://localhost:8000'

interface ApplicationRow {
  id: number
  job_url: string | null
  job_hash: string
  company: string | null
  title: string | null
  location: string | null
  contract_type: string | null
  status: ApplicationStatus
  match_score: number | null
  cv_path: string | null
  cover_letter_path: string | null
  notes: string | null
  created_at: string
  updated_at: string
}

type Filter = 'all' | ApplicationStatus

// ──────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────

function relativeDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const diffMs = Date.now() - d.getTime()
  const day = 24 * 60 * 60 * 1000
  const days = Math.floor(diffMs / day)
  if (days < 1) return "aujourd'hui"
  if (days === 1) return 'hier'
  if (days < 7) return `il y a ${days}j`
  if (days < 30) return `il y a ${Math.floor(days / 7)}sem`
  if (days < 365) return `il y a ${Math.floor(days / 30)}mois`
  return `il y a ${Math.floor(days / 365)}an${days >= 730 ? 's' : ''}`
}

function firstInitial(s: string | null | undefined): string {
  return (s ?? '').trim().slice(0, 1).toUpperCase() || '·'
}

function normalizeCompany(c: string | null): string {
  return (c ?? '').trim() || 'Sans société'
}

const STYLES = `
  :root {
    --bg: #f7f7f5;
    --pan: #ffffff;
    --ink: #23241f;
    --mut: #82837b;
    --faint: #b4b5ac;
    --line: #e9e9e3;
    --ac: #3d7d5a;
    --ac-soft: #e7f1eb;
    --bad: #b3503e;
    --bad-soft: #f6e7e2;
    --warn: #b88a3f;
    --warn-soft: #f5ebd9;
    --sans: 'Hanken Grotesk', system-ui, sans-serif;
    --mono: 'Spline Sans Mono', ui-monospace, monospace;
  }

  .tk-shell {
    max-width: 1100px;
    margin: 0 auto;
    padding: 28px 32px 60px;
    font-family: var(--sans);
    color: var(--ink);
  }

  /* Header */
  .tk-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 22px;
  }
  .tk-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--mut);
  }
  .tk-mark {
    width: 22px;
    height: 22px;
    border-radius: 7px;
    background: var(--ink);
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .tk-h1 {
    margin: 0;
    font-size: 28px;
    line-height: 1.18;
    font-weight: 700;
    letter-spacing: -0.025em;
    color: var(--ink);
  }
  .tk-reload {
    height: 34px;
    padding: 0 14px;
    border: 1px solid var(--line);
    border-radius: 9px;
    background: var(--pan);
    color: var(--mut);
    font-family: var(--mono);
    font-size: 11.5px;
    cursor: pointer;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
  }
  .tk-reload:hover {
    background: var(--ac-soft);
    border-color: var(--ac);
    color: var(--ac);
  }

  /* Stats strip */
  .tk-stats {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    margin-bottom: 22px;
  }
  .tk-stat {
    flex: 1 1 auto;
    min-width: 130px;
    background: var(--pan);
    border: 1px solid var(--line);
    border-radius: 13px;
    padding: 14px 16px;
  }
  .tk-stat-label {
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    letter-spacing: 0.04em;
    text-transform: lowercase;
    margin-bottom: 6px;
  }
  .tk-stat-val {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--ink);
  }
  .tk-stat-val.ac { color: var(--ac); }
  .tk-stat-val.bad { color: var(--bad); }

  /* Filter bar */
  .tk-filter {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 22px;
    padding: 12px 14px;
    border: 1px solid var(--line);
    border-radius: 13px;
    background: var(--pan);
  }
  .tk-chips { display: flex; gap: 6px; flex-wrap: wrap; }
  .tk-chip {
    font-family: var(--mono);
    font-size: 10.5px;
    letter-spacing: 0.02em;
    padding: 5px 11px;
    border-radius: 7px;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--mut);
    cursor: pointer;
    transition: all 0.15s;
  }
  .tk-chip:hover { color: var(--ink); border-color: var(--mut); }
  .tk-chip.active {
    background: var(--ink);
    color: #fff;
    border-color: var(--ink);
  }
  .tk-search {
    flex: 1;
    min-width: 200px;
    height: 32px;
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0 12px;
    background: var(--bg);
    font-family: var(--sans);
    font-size: 13px;
    color: var(--ink);
    outline: none;
  }
  .tk-search:focus { border-color: var(--ac); background: var(--pan); }

  /* Company group */
  .tk-group {
    margin-bottom: 18px;
    background: var(--pan);
    border: 1px solid var(--line);
    border-radius: 13px;
    overflow: hidden;
  }
  .tk-group-head {
    display: flex;
    align-items: center;
    gap: 11px;
    padding: 14px 16px;
    border-bottom: 1px solid var(--line);
    background: var(--bg);
    cursor: pointer;
    user-select: none;
  }
  .tk-group-head.collapsed { border-bottom: none; }
  .tk-logo {
    width: 28px;
    height: 28px;
    border-radius: 8px;
    background: #eceae2;
    border: 1px solid var(--line);
    font-size: 12px;
    font-weight: 700;
    color: #6c6d63;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .tk-group-name {
    font-size: 15px;
    font-weight: 700;
    letter-spacing: -0.01em;
    flex: 1;
  }
  .tk-group-count {
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    letter-spacing: 0.04em;
  }
  .tk-caret {
    color: var(--mut);
    font-size: 12px;
    transition: transform 0.15s;
  }
  .tk-group-head.collapsed .tk-caret { transform: rotate(-90deg); }

  /* Rows */
  .tk-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 180px 110px;
    gap: 14px;
    padding: 14px 16px;
    border-bottom: 1px solid var(--line);
    align-items: start;
  }
  .tk-row:last-child { border-bottom: none; }
  .tk-row-title {
    font-size: 14.5px;
    font-weight: 600;
    color: var(--ink);
    margin: 0 0 4px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tk-row-meta {
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    letter-spacing: 0.02em;
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }
  .tk-row-actions {
    display: flex;
    gap: 6px;
    margin-top: 8px;
    flex-wrap: wrap;
  }
  .tk-link {
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    text-decoration: none;
    border: 1px solid var(--line);
    padding: 3px 9px;
    border-radius: 6px;
    background: var(--bg);
    cursor: pointer;
    transition: all 0.15s;
  }
  .tk-link:hover {
    background: var(--ac-soft);
    color: var(--ac);
    border-color: var(--ac);
  }
  .tk-link:disabled { opacity: 0.5; cursor: default; }
  .tk-link.url:hover { background: #eef3ff; color: #3d5fa8; border-color: #3d5fa8; }
  .tk-notes {
    grid-column: 1 / -1;
    margin-top: 8px;
    width: 100%;
    min-height: 38px;
    padding: 8px 10px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--bg);
    font-family: var(--sans);
    font-size: 12.5px;
    color: var(--ink);
    resize: vertical;
    outline: none;
  }
  .tk-notes:focus { border-color: var(--ac); background: var(--pan); }
  .tk-notes::placeholder { color: var(--faint); }

  .tk-status-select {
    font-family: var(--mono);
    font-size: 10.5px;
    letter-spacing: 0.02em;
    padding: 6px 24px 6px 10px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--pan);
    color: var(--ink);
    cursor: pointer;
    appearance: none;
    -webkit-appearance: none;
    background-image:
      linear-gradient(45deg, transparent 50%, var(--mut) 50%),
      linear-gradient(135deg, var(--mut) 50%, transparent 50%);
    background-position: calc(100% - 12px) 50%, calc(100% - 8px) 50%;
    background-size: 4px 4px;
    background-repeat: no-repeat;
    transition: border-color 0.15s, background-color 0.15s;
    width: 100%;
  }
  .tk-status-select.progressed {
    background-color: var(--ac-soft);
    color: var(--ac);
    border-color: transparent;
  }
  .tk-status-select.warn {
    background-color: var(--warn-soft);
    color: var(--warn);
    border-color: transparent;
  }
  .tk-status-select.bad {
    background-color: var(--bad-soft);
    color: var(--bad);
    border-color: transparent;
  }

  .tk-saving {
    font-family: var(--mono);
    font-size: 9.5px;
    color: var(--ac);
    letter-spacing: 0.04em;
    align-self: center;
    text-align: right;
  }

  /* Empty / loading / error */
  .tk-empty, .tk-loading, .tk-error {
    text-align: center;
    padding: 80px 20px;
    color: var(--mut);
  }
  .tk-empty-h { font-size: 17px; font-weight: 600; color: var(--ink); margin-bottom: 8px; }
  .tk-error-msg {
    display: inline-block;
    font-family: var(--mono);
    font-size: 12px;
    color: var(--bad);
    background: var(--bad-soft);
    padding: 12px 18px;
    border-radius: 9px;
    margin-top: 12px;
  }
`

// ──────────────────────────────────────────────────────────────────────────
// Component
// ──────────────────────────────────────────────────────────────────────────

function statusVariantClass(status: ApplicationStatus): string {
  if (status === 'response_neg') return 'bad'
  if (status === 'response_pos') return 'progressed'
  if (status === 'interview') return 'progressed'
  if (status === 'applied' || status === 'followed_up') return 'progressed'
  return ''
}

export default function Tracker() {
  const [apps, setApps] = useState<ApplicationRow[] | null>(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [savingIds, setSavingIds] = useState<Set<number>>(new Set())

  // Debounce des PATCH notes par row
  const notesTimers = useRef<Map<number, number>>(new Map())

  async function load() {
    setError('')
    try {
      const r = await fetch(`${BACKEND_URL}/applications`)
      if (!r.ok) throw new Error(`Backend ${r.status}`)
      const data = (await r.json()) as ApplicationRow[]
      setApps(data)
    } catch (err) {
      if (err instanceof TypeError && /fetch|network/i.test(err.message)) {
        setError('Backend injoignable sur localhost:8000. Lance .\\dev.ps1 puis recharge.')
      } else {
        setError(err instanceof Error ? err.message : 'Erreur inconnue')
      }
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function patchOne(id: number, patch: { status?: string; notes?: string }) {
    setSavingIds((s) => new Set(s).add(id))
    try {
      const r = await fetch(`${BACKEND_URL}/applications/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!r.ok) {
        const txt = await r.text()
        throw new Error(`Backend ${r.status} — ${txt.slice(0, 140)}`)
      }
      const row = (await r.json()) as ApplicationRow
      // Patch local — on évite un round-trip /applications complet
      setApps((cur) =>
        cur ? cur.map((a) => (a.id === id ? { ...a, ...row } : a)) : cur,
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur inconnue')
    } finally {
      setSavingIds((s) => {
        const next = new Set(s)
        next.delete(id)
        return next
      })
    }
  }

  function onStatusChange(id: number, status: ApplicationStatus) {
    // Optimistic
    setApps((cur) => (cur ? cur.map((a) => (a.id === id ? { ...a, status } : a)) : cur))
    patchOne(id, { status })
  }

  function onNotesChange(id: number, notes: string) {
    setApps((cur) => (cur ? cur.map((a) => (a.id === id ? { ...a, notes } : a)) : cur))
    const t = notesTimers.current.get(id)
    if (t) window.clearTimeout(t)
    const handle = window.setTimeout(() => {
      patchOne(id, { notes })
      notesTimers.current.delete(id)
    }, 700)
    notesTimers.current.set(id, handle)
  }

  async function openCv(path: string) {
    try {
      const r = await fetch(`${BACKEND_URL}/open-file`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      })
      if (!r.ok) {
        const txt = await r.text()
        throw new Error(`Backend ${r.status} — ${txt.slice(0, 140)}`)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur inconnue')
    }
  }

  // Filtrage + tri (par date desc déjà fait par le backend)
  const filtered = useMemo(() => {
    if (!apps) return []
    const q = search.trim().toLowerCase()
    return apps.filter((a) => {
      if (filter !== 'all' && a.status !== filter) return false
      if (q) {
        const hay = `${a.company ?? ''} ${a.title ?? ''} ${a.location ?? ''}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [apps, filter, search])

  // Groupement par société (préserve l'ordre du backend = created_at desc)
  const groups = useMemo(() => {
    const m = new Map<string, ApplicationRow[]>()
    for (const a of filtered) {
      const key = normalizeCompany(a.company)
      if (!m.has(key)) m.set(key, [])
      m.get(key)!.push(a)
    }
    return Array.from(m.entries())
  }, [filtered])

  // Stats globales (sur apps non-filtrées)
  const stats = useMemo(() => {
    const s = {
      total: apps?.length ?? 0,
      seen: 0,
      applied: 0,
      followed_up: 0,
      interview: 0,
      response_pos: 0,
      response_neg: 0,
    }
    for (const a of apps ?? []) s[a.status] += 1
    return s
  }, [apps])

  function toggleGroup(name: string) {
    setCollapsed((s) => {
      const next = new Set(s)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  if (apps === null && !error) {
    return (
      <>
        <style>{STYLES}</style>
        <div className="tk-shell">
          <div className="tk-loading">Chargement des candidatures…</div>
        </div>
      </>
    )
  }

  return (
    <>
      <style>{STYLES}</style>
      <div className="tk-shell">
        <div className="tk-head">
          <div>
            <div className="tk-brand">
              <span className="tk-mark">J</span>
              Job Apply
            </div>
            <h1 className="tk-h1">Suivi des candidatures</h1>
          </div>
          <button className="tk-reload" onClick={load}>
            ↻ Recharger
          </button>
        </div>

        {error && (
          <div className="tk-error">
            <div className="tk-error-msg">{error}</div>
          </div>
        )}

        <div className="tk-stats">
          <div className="tk-stat">
            <div className="tk-stat-label">total</div>
            <div className="tk-stat-val">{stats.total}</div>
          </div>
          <div className="tk-stat">
            <div className="tk-stat-label">déjà postulé</div>
            <div className="tk-stat-val ac">{stats.applied}</div>
          </div>
          <div className="tk-stat">
            <div className="tk-stat-label">relancées</div>
            <div className="tk-stat-val ac">{stats.followed_up}</div>
          </div>
          <div className="tk-stat">
            <div className="tk-stat-label">entretiens</div>
            <div className="tk-stat-val ac">{stats.interview}</div>
          </div>
          <div className="tk-stat">
            <div className="tk-stat-label">réponses +</div>
            <div className="tk-stat-val ac">{stats.response_pos}</div>
          </div>
          <div className="tk-stat">
            <div className="tk-stat-label">réponses −</div>
            <div className="tk-stat-val bad">{stats.response_neg}</div>
          </div>
        </div>

        <div className="tk-filter">
          <div className="tk-chips">
            <button
              className={`tk-chip ${filter === 'all' ? 'active' : ''}`}
              onClick={() => setFilter('all')}
            >
              Tous
            </button>
            {APPLICATION_STATUSES.map((s) => (
              <button
                key={s}
                className={`tk-chip ${filter === s ? 'active' : ''}`}
                onClick={() => setFilter(s)}
              >
                {STATUS_LABELS[s]}
              </button>
            ))}
          </div>
          <input
            className="tk-search"
            type="text"
            placeholder="Rechercher (société, titre, lieu)…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {apps && apps.length === 0 && (
          <div className="tk-empty">
            <div className="tk-empty-h">Aucune candidature pour l'instant.</div>
            <div>
              Analyse une offre depuis le popup — elle apparaîtra ici
              automatiquement.
            </div>
          </div>
        )}

        {apps && apps.length > 0 && filtered.length === 0 && (
          <div className="tk-empty">
            <div className="tk-empty-h">Aucun résultat.</div>
            <div>Essaie d'élargir le filtre ou la recherche.</div>
          </div>
        )}

        {groups.map(([company, rows]) => {
          const isCollapsed = collapsed.has(company)
          return (
            <div key={company} className="tk-group">
              <div
                className={`tk-group-head ${isCollapsed ? 'collapsed' : ''}`}
                onClick={() => toggleGroup(company)}
              >
                <span className="tk-logo">{firstInitial(company)}</span>
                <span className="tk-group-name">{company}</span>
                <span className="tk-group-count">
                  {rows.length} candidature{rows.length > 1 ? 's' : ''}
                </span>
                <span className="tk-caret">▾</span>
              </div>
              {!isCollapsed &&
                rows.map((a) => {
                  const variantClass = statusVariantClass(a.status)
                  return (
                    <div key={a.id} className="tk-row">
                      <div>
                        <h3 className="tk-row-title">
                          {a.title || 'Sans titre'}
                        </h3>
                        <div className="tk-row-meta">
                          {a.location && <span>{a.location}</span>}
                          {a.contract_type && <span>· {a.contract_type}</span>}
                          <span>· vue {relativeDate(a.created_at)}</span>
                        </div>
                        <div className="tk-row-actions">
                          {a.job_url && (
                            <a
                              className="tk-link url"
                              href={a.job_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              ↗ offre
                            </a>
                          )}
                          {a.cv_path && (
                            <button
                              className="tk-link"
                              onClick={() => openCv(a.cv_path!)}
                            >
                              📄 CV
                            </button>
                          )}
                        </div>
                      </div>
                      <select
                        className={`tk-status-select ${
                          isProgressedStatus(a.status) ? variantClass : ''
                        }`}
                        value={a.status}
                        onChange={(e) =>
                          onStatusChange(a.id, e.target.value as ApplicationStatus)
                        }
                      >
                        {APPLICATION_STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {STATUS_LABELS[s]}
                          </option>
                        ))}
                      </select>
                      <div className="tk-saving">
                        {savingIds.has(a.id) ? '⟳ sauvegarde…' : ''}
                      </div>
                      <textarea
                        className="tk-notes"
                        placeholder="Notes (recruteur, date relance, salaire négocié, etc.)"
                        value={a.notes ?? ''}
                        onChange={(e) => onNotesChange(a.id, e.target.value)}
                      />
                    </div>
                  )
                })}
            </div>
          )
        })}
      </div>
    </>
  )
}
