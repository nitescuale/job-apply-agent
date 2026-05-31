import React, { useEffect, useState } from 'react'

const BACKEND_URL = 'http://localhost:8000'

const IS_MAC =
  typeof navigator !== 'undefined' && /mac/i.test(navigator.platform || navigator.userAgent)
const SHORTCUT_HINT = IS_MAC ? '⌘ ↵ pour postuler' : 'Ctrl ↵ pour postuler'
const SHORTCUT_KEYCAP = IS_MAC ? '⌘↵' : 'Ctrl ↵'

type Status =
  | 'idle'        // before any analysis
  | 'scraping'    // analysing the page
  | 'ready'       // result panel shown, ready to apply
  | 'applying'    // auto-apply in flight
  | 'applied'     // form filled successfully
  | 'error'       // analysis error
  | 'apply-error' // apply error (keep showing result)

interface OfferResult {
  url?: string
  title?: string
  company?: string
  location?: string
  contract_type?: string
  employment_type?: string
  salary?: string
  remote?: boolean | string
  experience_level?: string
  posted_date?: string
  valid_through?: string
  description?: string
  match_score?: number
  source?: string
  llm_used?: boolean
  llm_error?: string
  [key: string]: unknown
}

interface FillReport {
  filled: string[]
  skipped: { id: string; reason: string }[]
}

// ──────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────

function sendMessageToTab<T = unknown>(tabId: number, message: unknown): Promise<T> {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error('Impossible de contacter la page. Recharge-la et réessaie.'))
        return
      }
      resolve(response as T)
    })
  })
}

function formatRemote(v: boolean | string | undefined): string | null {
  if (v === undefined || v === null) return null
  if (typeof v === 'boolean') return v ? 'Oui' : 'Non'
  return String(v).trim() || null
}

function formatFrenchDate(input?: string): string | null {
  if (!input) return null
  const d = new Date(input)
  if (Number.isNaN(d.getTime())) return input
  return d
    .toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' })
    .replace('.', '.')
}

function firstInitial(s?: string): string {
  return (s ?? '').trim().slice(0, 1).toUpperCase() || '·'
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
    --sans: 'Hanken Grotesk', system-ui, sans-serif;
    --mono: 'Spline Sans Mono', ui-monospace, monospace;
  }

  .ja-panel {
    width: 400px;
    min-height: 480px;
    background: var(--bg);
    color: var(--ink);
    font-family: var(--sans);
    font-size: 14px;
    line-height: 1.5;
    display: flex;
    flex-direction: column;
  }

  /* top bar */
  .ja-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 18px;
    border-bottom: 1px solid var(--line);
    background: var(--pan);
    position: sticky;
    top: 0;
    z-index: 5;
  }
  .ja-brand {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .ja-mark {
    width: 18px;
    height: 18px;
    border-radius: 6px;
    background: var(--ink);
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--sans);
  }
  .ja-kbd {
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 3px 7px;
    background: var(--bg);
    white-space: nowrap;
  }

  /* body */
  .ja-body {
    padding: 22px 22px 0;
    overflow-y: auto;
    flex: 1;
  }
  .ja-tag {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.04em;
    color: var(--ac);
    background: var(--ac-soft);
    padding: 5px 10px;
    border-radius: 7px;
    margin-bottom: 16px;
  }
  .ja-tag.muted {
    color: var(--mut);
    background: #f0f0ea;
  }
  .ja-company {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-bottom: 9px;
  }
  .ja-logo {
    width: 26px;
    height: 26px;
    border-radius: 7px;
    background: #eceae2;
    border: 1px solid var(--line);
    font-size: 11px;
    font-weight: 700;
    color: #6c6d63;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .ja-company-name {
    font-size: 13px;
    color: var(--mut);
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ja-h1 {
    margin: 0 0 18px;
    font-size: 22px;
    line-height: 1.22;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--ink);
  }

  /* metadata table */
  .ja-rows {
    border: 1px solid var(--line);
    border-radius: 13px;
    overflow: hidden;
    background: var(--pan);
    margin: 0 0 18px;
  }
  .ja-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 11px 15px;
    border-bottom: 1px solid var(--line);
  }
  .ja-row:last-child { border-bottom: none; }
  .ja-dt {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--mut);
    letter-spacing: 0.02em;
  }
  .ja-dd {
    margin: 0;
    font-size: 13.5px;
    font-weight: 600;
    color: var(--ink);
    text-align: right;
    max-width: 60%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ja-dd.empty {
    color: var(--faint);
    font-weight: 500;
  }

  .ja-desc {
    color: #54564d;
    font-size: 13px;
    line-height: 1.6;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
    margin: 0 0 22px;
  }
  .ja-desc.expanded {
    display: block;
    -webkit-line-clamp: unset;
    -webkit-box-orient: unset;
  }
  .ja-desc-more {
    background: none;
    border: none;
    padding: 0;
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    cursor: pointer;
    letter-spacing: 0.02em;
    margin-bottom: 18px;
  }
  .ja-desc-more:hover { color: var(--ink); }

  /* footer */
  .ja-foot {
    margin-top: auto;
    padding: 14px 18px 18px;
    border-top: 1px solid var(--line);
    background: var(--pan);
    display: flex;
    gap: 10px;
    align-items: center;
    flex-direction: column;
    position: sticky;
    bottom: 0;
  }
  .ja-foot-row {
    display: flex;
    gap: 10px;
    align-items: center;
    width: 100%;
  }
  .ja-cta {
    flex: 1;
    height: 46px;
    border: none;
    border-radius: 11px;
    background: var(--ink);
    color: #fff;
    font-family: var(--sans);
    font-size: 13.5px;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    transition: opacity 0.15s, transform 0.1s, background 0.15s;
  }
  .ja-cta:hover { opacity: 0.9; }
  .ja-cta:active { transform: translateY(1px); }
  .ja-cta:disabled { cursor: default; opacity: 0.65; }
  .ja-cta.success {
    background: var(--ac);
  }
  .ja-keycap {
    font-family: var(--mono);
    font-size: 10px;
    opacity: 0.6;
    border: 1px solid rgba(255, 255, 255, 0.25);
    border-radius: 5px;
    padding: 2px 5px;
  }
  .ja-icon {
    width: 46px;
    height: 46px;
    border-radius: 11px;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--mut);
    font-size: 16px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.15s, color 0.15s;
    flex-shrink: 0;
  }
  .ja-icon:hover { background: #efefe9; color: var(--ink); }
  .ja-icon:disabled { cursor: default; opacity: 0.5; }

  .ja-status-line {
    width: 100%;
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    letter-spacing: 0.02em;
    text-align: center;
  }
  .ja-status-line .ok { color: var(--ac); }
  .ja-status-line .ko { color: var(--bad); }
  .ja-status-line .reset {
    background: none;
    border: none;
    color: var(--mut);
    font-family: var(--mono);
    font-size: 10.5px;
    cursor: pointer;
    text-decoration: underline;
    text-decoration-color: var(--line);
    text-underline-offset: 3px;
    padding: 0;
    margin-left: 6px;
  }
  .ja-status-line .reset:hover { color: var(--ink); text-decoration-color: var(--mut); }

  /* idle */
  .ja-idle {
    padding: 60px 22px 40px;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
  .ja-idle h1 {
    margin: 0;
    font-size: 26px;
    line-height: 1.18;
    font-weight: 700;
    letter-spacing: -0.025em;
    color: var(--ink);
  }
  .ja-idle p {
    margin: 0 0 16px;
    font-size: 13.5px;
    color: var(--mut);
    line-height: 1.55;
  }
  .ja-idle .ja-cta { width: 100%; flex: none; }

  /* loading */
  .ja-loading {
    padding: 56px 22px 40px;
  }
  .ja-loading-step {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.04em;
    color: var(--mut);
    margin-bottom: 12px;
  }
  .ja-loading-step .idx { color: var(--ac); }
  .ja-loading-msg {
    font-size: 18px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ink);
    margin-bottom: 22px;
  }
  .ja-bar-scan {
    height: 2px;
    background: var(--line);
    border-radius: 2px;
    overflow: hidden;
    position: relative;
  }
  .ja-bar-scan::after {
    content: '';
    position: absolute;
    inset: 0;
    width: 40%;
    background: linear-gradient(90deg, transparent, var(--ac), transparent);
    animation: ja-scan 1.6s ease-in-out infinite;
  }
  @keyframes ja-scan {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(350%); }
  }

  /* error */
  .ja-error {
    padding: 56px 22px 40px;
  }
  .ja-err-label {
    font-family: var(--mono);
    font-size: 10.5px;
    letter-spacing: 0.04em;
    color: var(--bad);
    margin-bottom: 12px;
  }
  .ja-err-msg {
    font-size: 18px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ink);
    margin-bottom: 12px;
  }
  .ja-err-detail {
    font-family: var(--mono);
    font-size: 11px;
    line-height: 1.5;
    color: var(--mut);
    background: var(--pan);
    border: 1px solid var(--line);
    border-radius: 11px;
    padding: 12px 14px;
    margin-bottom: 18px;
    word-break: break-word;
  }

  /* spinner inside CTA */
  .ja-spin {
    width: 14px;
    height: 14px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: ja-spin 0.8s linear infinite;
  }
  @keyframes ja-spin {
    to { transform: rotate(360deg); }
  }
`

// ──────────────────────────────────────────────────────────────────────────
// Sub-components
// ──────────────────────────────────────────────────────────────────────────

function TopBar({ showShortcut }: { showShortcut: boolean }) {
  return (
    <div className="ja-bar">
      <div className="ja-brand">
        <span className="ja-mark">J</span>
        Job Apply
      </div>
      {showShortcut && <span className="ja-kbd">{SHORTCUT_HINT}</span>}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string | null }) {
  const empty = value === null || value === ''
  return (
    <div className="ja-row">
      <dt className="ja-dt">{label}</dt>
      <dd className={`ja-dd${empty ? ' empty' : ''}`}>{empty ? 'non précisé' : value}</dd>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Popup
// ──────────────────────────────────────────────────────────────────────────

export default function Popup() {
  const [status, setStatus] = useState<Status>('idle')
  const [result, setResult] = useState<OfferResult | null>(null)
  const [error, setError] = useState('')
  const [applyError, setApplyError] = useState('')
  const [fillReport, setFillReport] = useState<FillReport | null>(null)
  const [descExpanded, setDescExpanded] = useState(false)
  const [loadingStep, setLoadingStep] = useState({ idx: 1, label: 'Capture de la page' })

  async function handleAnalyze() {
    setStatus('scraping')
    setError('')
    setApplyError('')
    setFillReport(null)
    setDescExpanded(false)
    setLoadingStep({ idx: 1, label: 'Capture de la page' })

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      if (!tab.id) throw new Error('Onglet non trouvé')

      const response = await sendMessageToTab<{ html: string; url: string }>(tab.id, {
        type: 'CAPTURE_JOB_HTML',
      })
      if (!response?.html) throw new Error('Impossible de capturer le HTML')

      setLoadingStep({ idx: 2, label: 'Extraction + filtrage LLM' })

      const r = await fetch(`${BACKEND_URL}/scrape-job`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_url: response.url, job_html: response.html }),
      })

      if (!r.ok) {
        const txt = await r.text()
        throw new Error(`Backend ${r.status} — ${txt.slice(0, 140)}`)
      }

      const data: OfferResult = await r.json()
      setResult(data)
      setStatus('ready')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur inconnue')
      setStatus('error')
    }
  }

  async function handleApply() {
    if (status === 'applying' || !result) return
    setStatus('applying')
    setApplyError('')
    setFillReport(null)

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      if (!tab.id) throw new Error('Onglet non trouvé')

      const detected = await sendMessageToTab<{ schema: { fields: unknown[] } | null }>(tab.id, {
        type: 'DETECT_FORM',
      })
      const schema = detected?.schema
      if (!schema || !schema.fields || schema.fields.length === 0) {
        throw new Error('Aucun formulaire détecté sur cette page')
      }

      const context = {
        title: result.title,
        company: result.company,
        location: result.location,
      }
      const res = await fetch(`${BACKEND_URL}/fill-form`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ form_schema: schema, context }),
      })
      if (!res.ok) {
        const txt = await res.text()
        throw new Error(`Backend ${res.status} — ${txt.slice(0, 160)}`)
      }
      const fillPayload = await res.json()

      const report = await sendMessageToTab<FillReport>(tab.id, {
        type: 'FILL_FORM',
        payload: fillPayload,
      })
      setFillReport(report)
      setStatus('applied')
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : 'Erreur inconnue')
      setStatus('apply-error')
    }
  }

  function handleOpenOriginal() {
    if (result?.url) {
      chrome.tabs.create({ url: result.url })
    }
  }

  function handleReset() {
    setStatus('idle')
    setResult(null)
    setError('')
    setApplyError('')
    setFillReport(null)
    setDescExpanded(false)
  }

  // Cmd+Enter / Ctrl+Enter triggers Postuler when result is ready
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        if (status === 'ready' || status === 'apply-error') {
          e.preventDefault()
          handleApply()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, result])

  // ── Render ─────────────────────────────────────────────────────────────

  if (status === 'idle') {
    return (
      <>
        <style>{STYLES}</style>
        <div className="ja-panel">
          <TopBar showShortcut={false} />
          <div className="ja-idle">
            <h1>Analyser cette page.</h1>
            <p>
              Scraping structurel, filtrage LLM,{' '}essentiels structurés. Tu valides et tu
              postules.
            </p>
            <button className="ja-cta" onClick={handleAnalyze}>
              Analyser la page
            </button>
          </div>
        </div>
      </>
    )
  }

  if (status === 'scraping') {
    return (
      <>
        <style>{STYLES}</style>
        <div className="ja-panel">
          <TopBar showShortcut={false} />
          <div className="ja-loading">
            <div className="ja-loading-step">
              étape <span className="idx">0{loadingStep.idx}</span> / 02
            </div>
            <div className="ja-loading-msg">{loadingStep.label}…</div>
            <div className="ja-bar-scan" />
          </div>
        </div>
      </>
    )
  }

  if (status === 'error') {
    return (
      <>
        <style>{STYLES}</style>
        <div className="ja-panel">
          <TopBar showShortcut={false} />
          <div className="ja-error">
            <div className="ja-err-label">erreur · analyse</div>
            <div className="ja-err-msg">Échec de l'analyse.</div>
            <div className="ja-err-detail">{error}</div>
            <button className="ja-cta" onClick={handleReset}>
              Réessayer
            </button>
          </div>
        </div>
      </>
    )
  }

  // status ∈ { ready, applying, applied, apply-error }
  const r = result ?? {}
  const contract = r.contract_type ?? r.employment_type ?? null
  const remote = formatRemote(r.remote)
  const exp = r.experience_level ?? null
  const published = formatFrenchDate(r.posted_date)
  const expires = formatFrenchDate(r.valid_through)
  const matchScore =
    typeof r.match_score === 'number' && Number.isFinite(r.match_score)
      ? Math.round(r.match_score)
      : null

  const companyLine = [r.company, r.location].filter(Boolean).join(' · ')

  const isApplying = status === 'applying'
  const isApplied = status === 'applied'
  const isApplyError = status === 'apply-error'

  return (
    <>
      <style>{STYLES}</style>
      <div className="ja-panel">
        <TopBar showShortcut={status === 'ready'} />

        <div className="ja-body">
          {r.llm_used ? (
            <span className="ja-tag">
              ✦ filtré par LLM
              {matchScore !== null && <> · {matchScore}% match</>}
            </span>
          ) : (
            <span className="ja-tag muted">scraping brut</span>
          )}

          {(r.company || r.location) && (
            <div className="ja-company">
              <span className="ja-logo">{firstInitial(r.company)}</span>
              <span className="ja-company-name">{companyLine || '—'}</span>
            </div>
          )}

          <h1 className="ja-h1">{r.title || 'Sans titre'}</h1>

          <dl className="ja-rows">
            <Row label="contrat" value={contract} />
            <Row label="salaire" value={r.salary ?? null} />
            <Row label="télétravail" value={remote} />
            <Row label="expérience" value={exp} />
            <Row label="publié" value={published} />
            {expires && <Row label="expire" value={expires} />}
          </dl>

          {r.description && (
            <>
              <p className={`ja-desc${descExpanded ? ' expanded' : ''}`}>{r.description}</p>
              {r.description.length > 180 && (
                <button
                  className="ja-desc-more"
                  onClick={() => setDescExpanded((v) => !v)}
                >
                  {descExpanded ? '— Réduire' : '+ Lire la suite'}
                </button>
              )}
            </>
          )}
        </div>

        <div className="ja-foot">
          <div className="ja-foot-row">
            <button
              className={`ja-cta${isApplied ? ' success' : ''}`}
              onClick={handleApply}
              disabled={isApplying || isApplied}
            >
              {isApplying && (
                <>
                  <span className="ja-spin" /> Envoi…
                </>
              )}
              {isApplied && <>✓ Formulaire rempli</>}
              {(status === 'ready' || isApplyError) && (
                <>
                  Postuler <span className="ja-keycap">{SHORTCUT_KEYCAP}</span>
                </>
              )}
            </button>
            <button
              className="ja-icon"
              onClick={handleOpenOriginal}
              disabled={!r.url}
              aria-label="Voir l'offre d'origine"
              title="Voir l'offre d'origine"
            >
              ↗
            </button>
          </div>

          {isApplied && fillReport && (
            <div className="ja-status-line">
              <span className="ok">{fillReport.filled.length} rempli{fillReport.filled.length > 1 ? 's' : ''}</span>
              {fillReport.skipped.length > 0 && (
                <>
                  {' · '}
                  <span className="ko">
                    {fillReport.skipped.length} ignoré{fillReport.skipped.length > 1 ? 's' : ''}
                  </span>
                </>
              )}
              <button className="reset" onClick={handleReset}>
                nouvelle analyse
              </button>
            </div>
          )}

          {isApplyError && (
            <div className="ja-status-line">
              <span className="ko">{applyError}</span>
              <button className="reset" onClick={handleReset}>
                réinitialiser
              </button>
            </div>
          )}

          {status === 'ready' && (
            <div className="ja-status-line">
              <button className="reset" onClick={handleReset}>
                nouvelle analyse
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
