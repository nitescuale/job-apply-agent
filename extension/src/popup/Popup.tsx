import React, { useState } from 'react'

const BACKEND_URL = 'http://localhost:8000'

type State = 'idle' | 'loading' | 'result' | 'error'

interface ScrapeResult {
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
  skills?: string[]
  missions?: string[]
  summary?: string
  description?: string
  source?: string
  llm_used?: boolean
  llm_error?: string
  [key: string]: unknown
}

function sendMessageToTab<T = unknown>(
  tabId: number,
  message: unknown,
): Promise<T> {
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

interface FillReport {
  filled: string[]
  skipped: { id: string; reason: string }[]
}

function fmtRemote(v: boolean | string | undefined): string | undefined {
  if (v === undefined || v === null) return undefined
  if (typeof v === 'boolean') return v ? 'Oui' : 'Non'
  return v
}

const STYLES = `
  :root {
    --bg: #0a0a0c;
    --surface: #131318;
    --surface-2: #1c1c23;
    --border: #27272e;
    --border-strong: #3a3a44;
    --text: #ebe6dd;
    --text-2: #8b8580;
    --text-3: #5c5853;
    --accent: #e8b07c;
    --accent-soft: #3a2a1a;
    --good: #9fbf8a;
    --bad: #d97264;
    --serif: 'Fraunces', 'Times New Roman', serif;
    --sans: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    --mono: 'JetBrains Mono', 'SF Mono', Menlo, monospace;
  }

  .jp-root {
    width: 440px;
    padding: 26px 28px 24px;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    position: relative;
    overflow: hidden;
  }
  .jp-root::before {
    content: '';
    position: absolute;
    inset: 0;
    background:
      radial-gradient(circle at 80% -10%, rgba(232, 176, 124, 0.07), transparent 50%),
      radial-gradient(circle at 0% 100%, rgba(232, 176, 124, 0.04), transparent 40%);
    pointer-events: none;
  }
  .jp-root > * { position: relative; }

  .jp-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 22px;
  }
  .jp-brand {
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 0.22em;
    color: var(--text-2);
    text-transform: uppercase;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .jp-brand-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 8px var(--accent);
  }

  .jp-pill {
    font-family: var(--mono);
    font-size: 9.5px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    padding: 4px 8px 4px 10px;
    border: 1px solid var(--border-strong);
    border-radius: 999px;
    color: var(--text-2);
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--surface);
  }
  .jp-pill.llm {
    color: var(--accent);
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .jp-pill.llm::before {
    content: '✦';
    font-size: 11px;
  }

  .jp-display {
    font-family: var(--serif);
    font-weight: 700;
    font-style: italic;
    font-size: 34px;
    line-height: 1.04;
    letter-spacing: -0.015em;
    color: var(--text);
    margin: 0;
  }
  .jp-display .accent { color: var(--accent); font-style: normal; }

  .jp-subline {
    font-family: var(--mono);
    font-size: 10.5px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--text-2);
    margin-top: 16px;
    line-height: 1.6;
  }
  .jp-subline .sep { color: var(--text-3); margin: 0 8px; }

  .jp-rule {
    height: 1px;
    background: var(--border);
    margin: 22px 0;
  }
  .jp-rule-short {
    height: 1px;
    width: 36px;
    background: var(--accent);
    margin: 18px 0;
  }

  .jp-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 18px 24px;
  }
  .jp-cell-label {
    font-family: var(--mono);
    font-size: 9.5px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--text-3);
    margin-bottom: 4px;
  }
  .jp-cell-value {
    font-family: var(--sans);
    font-size: 14px;
    font-weight: 400;
    color: var(--text);
    line-height: 1.35;
    word-break: break-word;
  }
  .jp-cell-value.empty { color: var(--text-3); }

  .jp-section-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--text-2);
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 14px;
  }
  .jp-section-label::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  .jp-tags { display: flex; flex-wrap: wrap; gap: 6px; }
  .jp-tag {
    font-family: var(--mono);
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.06em;
    padding: 5px 10px;
    border: 1px solid var(--border-strong);
    color: var(--text);
    background: transparent;
    border-radius: 4px;
    transition: all 0.15s ease;
  }
  .jp-tag:hover {
    border-color: var(--accent);
    color: var(--accent);
    background: var(--accent-soft);
  }

  .jp-mission {
    display: flex;
    gap: 14px;
    align-items: flex-start;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
  }
  .jp-mission:last-child { border-bottom: none; }
  .jp-mission-num {
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: 0.05em;
    line-height: 1.6;
    flex-shrink: 0;
    min-width: 22px;
  }
  .jp-mission-text {
    font-family: var(--sans);
    font-size: 13.5px;
    line-height: 1.55;
    color: var(--text);
  }

  .jp-summary {
    font-family: var(--serif);
    font-style: italic;
    font-size: 15px;
    line-height: 1.55;
    color: var(--text);
    border-left: 2px solid var(--accent);
    padding: 4px 0 4px 14px;
  }

  .jp-description {
    max-height: 140px;
    overflow-y: auto;
    font-family: var(--sans);
    font-size: 12.5px;
    line-height: 1.65;
    color: var(--text-2);
    padding: 12px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
  }

  .jp-actions {
    display: flex;
    gap: 8px;
    margin-top: 24px;
  }
  .jp-btn {
    flex: 1;
    font-family: var(--mono);
    font-size: 10.5px;
    font-weight: 700;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    padding: 14px 16px;
    border: 1px solid var(--border-strong);
    background: transparent;
    color: var(--text);
    cursor: pointer;
    border-radius: 4px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    transition: all 0.15s ease;
    position: relative;
  }
  .jp-btn:hover {
    border-color: var(--accent);
    color: var(--accent);
    background: var(--accent-soft);
  }
  .jp-btn.primary {
    background: var(--accent);
    color: #1a1208;
    border-color: var(--accent);
  }
  .jp-btn.primary:hover {
    background: #f1c294;
    border-color: #f1c294;
    color: #1a1208;
  }
  .jp-btn .arrow {
    display: inline-block;
    transition: transform 0.2s ease;
  }
  .jp-btn:hover .arrow { transform: translateX(3px); }

  /* --- idle --- */
  .jp-idle-title {
    font-family: var(--serif);
    font-weight: 800;
    font-size: 44px;
    line-height: 0.98;
    letter-spacing: -0.02em;
    color: var(--text);
    margin: 36px 0 0;
  }
  .jp-idle-title em {
    font-style: italic;
    font-weight: 600;
    color: var(--accent);
  }
  .jp-idle-tag {
    font-family: var(--mono);
    font-size: 10.5px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--text-2);
    margin: 24px 0 30px;
    line-height: 1.7;
  }
  .jp-idle-tag .dot { color: var(--accent); margin: 0 8px; }

  /* --- loading --- */
  .jp-loading-step {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--text-2);
    margin: 28px 0 14px;
  }
  .jp-loading-step .idx { color: var(--accent); }
  .jp-loading-msg {
    font-family: var(--serif);
    font-style: italic;
    font-size: 22px;
    color: var(--text);
    line-height: 1.2;
    margin-bottom: 28px;
  }
  .jp-loading-bar {
    height: 2px;
    background: var(--border);
    border-radius: 2px;
    overflow: hidden;
    position: relative;
  }
  .jp-loading-bar::after {
    content: '';
    position: absolute;
    inset: 0;
    width: 40%;
    background: linear-gradient(90deg, transparent, var(--accent), transparent);
    animation: jp-scan 1.6s ease-in-out infinite;
  }
  @keyframes jp-scan {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(350%); }
  }

  /* --- error --- */
  .jp-err-code {
    font-family: var(--mono);
    font-size: 10.5px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--bad);
    margin: 28px 0 12px;
  }
  .jp-err-msg {
    font-family: var(--serif);
    font-weight: 700;
    font-style: italic;
    font-size: 26px;
    line-height: 1.15;
    color: var(--text);
    margin-bottom: 24px;
  }

  /* --- fill report --- */
  .jp-fill {
    padding: 14px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--surface);
    margin-top: 18px;
  }
  .jp-fill-line {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.05em;
    color: var(--text-2);
    line-height: 1.6;
  }
  .jp-fill-line .ok { color: var(--good); }
  .jp-fill-line .ko { color: var(--bad); }
  .jp-fill-list {
    margin: 8px 0 0;
    padding: 0;
    list-style: none;
  }
  .jp-fill-list li {
    font-family: var(--mono);
    font-size: 10.5px;
    line-height: 1.7;
    color: var(--text-3);
    padding-left: 18px;
    position: relative;
  }
  .jp-fill-list li::before {
    content: '·';
    position: absolute;
    left: 6px;
    color: var(--accent);
  }
  .jp-fill-list.skipped li::before { color: var(--bad); }

  /* fade-in on mount */
  .jp-fade { animation: jp-fade-in 0.4s ease both; }
  .jp-fade-1 { animation-delay: 0.05s; }
  .jp-fade-2 { animation-delay: 0.12s; }
  .jp-fade-3 { animation-delay: 0.2s; }
  .jp-fade-4 { animation-delay: 0.28s; }
  @keyframes jp-fade-in {
    from { opacity: 0; transform: translateY(6px); }
    to { opacity: 1; transform: translateY(0); }
  }
`

function Header({ source, llmUsed }: { source?: string; llmUsed?: boolean }) {
  return (
    <div className="jp-header">
      <div className="jp-brand">
        <span className="jp-brand-dot" />
        Job · Apply
      </div>
      {source && (
        <span className={`jp-pill ${llmUsed ? 'llm' : ''}`}>
          {llmUsed ? 'LLM · enrichi' : source}
        </span>
      )}
    </div>
  )
}

function Cell({ label, value }: { label: string; value?: React.ReactNode }) {
  const display = value === undefined || value === null || value === '' ? '—' : value
  const empty = display === '—'
  return (
    <div>
      <div className="jp-cell-label">{label}</div>
      <div className={`jp-cell-value${empty ? ' empty' : ''}`}>{display}</div>
    </div>
  )
}

export default function Popup() {
  const [state, setState] = useState<State>('idle')
  const [result, setResult] = useState<ScrapeResult | null>(null)
  const [error, setError] = useState('')
  const [step, setStep] = useState('Capture du HTML')
  const [stepIdx, setStepIdx] = useState(1)
  const [copied, setCopied] = useState(false)
  const [fillState, setFillState] = useState<'idle' | 'filling' | 'done' | 'error'>('idle')
  const [fillReport, setFillReport] = useState<FillReport | null>(null)
  const [fillError, setFillError] = useState('')

  async function handleAnalyze() {
    setState('loading')
    setStep('Capture du HTML')
    setStepIdx(1)

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      if (!tab.id) throw new Error('Onglet non trouvé')

      const response = await sendMessageToTab<{ html: string; url: string }>(
        tab.id,
        { type: 'CAPTURE_JOB_HTML' },
      )
      if (!response?.html) throw new Error('Impossible de capturer le HTML')

      setStep('Extraction + filtrage LLM')
      setStepIdx(2)

      const r = await fetch(`${BACKEND_URL}/scrape-job`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_url: response.url, job_html: response.html }),
      })

      if (!r.ok) {
        const txt = await r.text()
        throw new Error(`Backend ${r.status} — ${txt.slice(0, 120)}`)
      }

      setResult(await r.json())
      setState('result')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur inconnue')
      setState('error')
    }
  }

  async function handleCopy() {
    if (!result) return
    try {
      await navigator.clipboard.writeText(JSON.stringify(result, null, 2))
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      setError('Copie impossible')
      setState('error')
    }
  }

  function handleReset() {
    setState('idle')
    setResult(null)
    setError('')
    setFillState('idle')
    setFillReport(null)
    setFillError('')
  }

  async function handleFillForm() {
    setFillState('filling')
    setFillError('')
    setFillReport(null)

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      if (!tab.id) throw new Error('Onglet non trouvé')

      // 1. Détecte le formulaire sur la page
      const detected = await sendMessageToTab<{ schema: { fields: unknown[] } | null }>(
        tab.id,
        { type: 'DETECT_FORM' },
      )
      const schema = detected?.schema
      if (!schema || !schema.fields || schema.fields.length === 0) {
        throw new Error('Aucun formulaire détecté sur cette page')
      }

      // 2. Backend mappe les champs au profil via Gemini
      const r = result ?? {}
      const context = { title: r.title, company: r.company, location: r.location }
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

      // 3. Content script applique les valeurs
      const report = await sendMessageToTab<FillReport>(tab.id, {
        type: 'FILL_FORM',
        payload: fillPayload,
      })

      setFillReport(report)
      setFillState('done')
    } catch (err) {
      setFillError(err instanceof Error ? err.message : 'Erreur inconnue')
      setFillState('error')
    }
  }

  if (state === 'idle') {
    return (
      <>
        <style>{STYLES}</style>
        <div className="jp-root">
          <Header />
          <h1 className="jp-idle-title jp-fade jp-fade-1">
            Analyser<em>.</em>
          </h1>
          <div className="jp-idle-tag jp-fade jp-fade-2">
            Scraping structurel
            <span className="dot">·</span>
            Filtrage LLM
            <span className="dot">·</span>
            Essentiels
          </div>
          <div className="jp-fade jp-fade-3">
            <button className="jp-btn primary" onClick={handleAnalyze} style={{ width: '100%' }}>
              Analyser la page <span className="arrow">→</span>
            </button>
          </div>
          <div className="jp-rule" style={{ marginTop: 26, marginBottom: 0 }} />
        </div>
      </>
    )
  }

  if (state === 'loading') {
    return (
      <>
        <style>{STYLES}</style>
        <div className="jp-root">
          <Header />
          <div className="jp-loading-step">
            étape <span className="idx">0{stepIdx}</span> / 02
          </div>
          <div className="jp-loading-msg">{step}…</div>
          <div className="jp-loading-bar" />
        </div>
      </>
    )
  }

  if (state === 'error') {
    return (
      <>
        <style>{STYLES}</style>
        <div className="jp-root">
          <Header />
          <div className="jp-err-code">ERR · 01</div>
          <div className="jp-err-msg">Échec de l'analyse.</div>
          <div className="jp-description" style={{ borderColor: '#3a1c18', color: 'var(--text-2)' }}>
            {error}
          </div>
          <div className="jp-actions">
            <button className="jp-btn primary" onClick={handleReset} style={{ flex: 1 }}>
              Réessayer <span className="arrow">↻</span>
            </button>
          </div>
        </div>
      </>
    )
  }

  // result
  const r = result ?? {}
  const skills = Array.isArray(r.skills) ? r.skills : []
  const missions = Array.isArray(r.missions) ? r.missions : []
  const contract = r.contract_type ?? r.employment_type

  return (
    <>
      <style>{STYLES}</style>
      <div className="jp-root">
        <Header source={r.source} llmUsed={r.llm_used} />

        {r.llm_error && (
          <div
            style={{
              fontFamily: 'var(--mono)',
              fontSize: 10.5,
              letterSpacing: '0.05em',
              padding: '10px 12px',
              border: '1px solid #3a1c18',
              background: '#1a0d0a',
              color: 'var(--bad)',
              borderRadius: 4,
              marginBottom: 18,
              lineHeight: 1.5,
            }}
          >
            LLM_ERROR · {r.llm_error}
          </div>
        )}

        <h1 className="jp-display jp-fade jp-fade-1">
          {r.title ?? <span style={{ color: 'var(--text-3)' }}>Sans titre</span>}
        </h1>

        {(r.company || r.location) && (
          <div className="jp-subline jp-fade jp-fade-2">
            {r.company}
            {r.company && r.location && <span className="sep">·</span>}
            {r.location}
          </div>
        )}

        <div className="jp-rule-short jp-fade jp-fade-2" />

        <div className="jp-grid jp-fade jp-fade-3">
          <Cell label="Contrat" value={contract} />
          <Cell label="Salaire" value={r.salary} />
          <Cell label="Télétravail" value={fmtRemote(r.remote)} />
          <Cell label="Expérience" value={r.experience_level} />
          <Cell label="Publié" value={r.posted_date} />
          <Cell label="Expire" value={r.valid_through} />
        </div>

        {r.summary && (
          <>
            <div className="jp-rule" />
            <div className="jp-summary jp-fade">{r.summary}</div>
          </>
        )}

        {skills.length > 0 && (
          <>
            <div className="jp-rule" />
            <div className="jp-section-label">Compétences · {skills.length}</div>
            <div className="jp-tags">
              {skills.map((s, i) => (
                <span key={i} className="jp-tag">
                  {String(s)}
                </span>
              ))}
            </div>
          </>
        )}

        {missions.length > 0 && (
          <>
            <div className="jp-rule" />
            <div className="jp-section-label">Missions · {missions.length}</div>
            <div>
              {missions.map((m, i) => (
                <div key={i} className="jp-mission">
                  <div className="jp-mission-num">{String(i + 1).padStart(2, '0')}·</div>
                  <div className="jp-mission-text">{String(m)}</div>
                </div>
              ))}
            </div>
          </>
        )}

        {r.description && (
          <>
            <div className="jp-rule" />
            <div className="jp-section-label">Description brute</div>
            <div className="jp-description">{r.description}</div>
          </>
        )}

        <div className="jp-rule" />
        <div className="jp-section-label">Candidature</div>

        {fillState === 'idle' && (
          <button
            className="jp-btn primary"
            onClick={handleFillForm}
            style={{ width: '100%' }}
          >
            Remplir le formulaire <span className="arrow">→</span>
          </button>
        )}

        {fillState === 'filling' && (
          <div className="jp-fill">
            <div className="jp-fill-line">remplissage en cours…</div>
            <div className="jp-loading-bar" style={{ marginTop: 10 }} />
          </div>
        )}

        {fillState === 'done' && fillReport && (
          <div className="jp-fill">
            <div className="jp-fill-line">
              <span className="ok">✓</span> {fillReport.filled.length} rempli
              {fillReport.filled.length > 1 ? 's' : ''}
              {fillReport.skipped.length > 0 && (
                <>
                  {' '}
                  <span className="ko">·</span> {fillReport.skipped.length} ignoré
                  {fillReport.skipped.length > 1 ? 's' : ''}
                </>
              )}
            </div>
            {fillReport.skipped.length > 0 && (
              <ul className="jp-fill-list skipped">
                {fillReport.skipped.map((s) => (
                  <li key={s.id}>
                    {s.id} — {s.reason}
                  </li>
                ))}
              </ul>
            )}
            <button
              className="jp-btn"
              onClick={handleFillForm}
              style={{ width: '100%', marginTop: 12 }}
            >
              Recommencer <span className="arrow">↻</span>
            </button>
          </div>
        )}

        {fillState === 'error' && (
          <div className="jp-fill" style={{ borderColor: '#3a1c18', background: '#1a0d0a' }}>
            <div className="jp-fill-line" style={{ color: 'var(--bad)' }}>
              FILL_ERROR · {fillError}
            </div>
            <button
              className="jp-btn"
              onClick={handleFillForm}
              style={{ width: '100%', marginTop: 12 }}
            >
              Réessayer <span className="arrow">↻</span>
            </button>
          </div>
        )}

        <div className="jp-actions">
          <button className="jp-btn primary" onClick={handleCopy}>
            {copied ? 'Copié ✓' : 'Copier JSON'}
          </button>
          <button className="jp-btn" onClick={handleReset}>
            Nouvelle <span className="arrow">↻</span>
          </button>
        </div>
      </div>
    </>
  )
}
