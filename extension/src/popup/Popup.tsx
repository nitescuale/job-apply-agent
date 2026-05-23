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
  [key: string]: unknown
}

function sendMessageToTab(
  tabId: number,
  message: unknown,
): Promise<{ html: string; url: string }> {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error('Impossible de contacter la page. Recharge-la et réessaie.'))
        return
      }
      resolve(response as { html: string; url: string })
    })
  })
}

function fmtRemote(v: boolean | string | undefined): string | undefined {
  if (v === undefined || v === null) return undefined
  if (typeof v === 'boolean') return v ? 'Oui' : 'Non'
  return v
}

export default function Popup() {
  const [state, setState] = useState<State>('idle')
  const [result, setResult] = useState<ScrapeResult | null>(null)
  const [error, setError] = useState('')
  const [step, setStep] = useState('')
  const [copied, setCopied] = useState(false)

  async function handleAnalyze() {
    setState('loading')
    setStep('Capture du HTML…')

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      if (!tab.id) throw new Error('Onglet non trouvé')

      const response = await sendMessageToTab(tab.id, { type: 'CAPTURE_JOB_HTML' })
      if (!response?.html) throw new Error('Impossible de capturer le HTML')

      setStep('Extraction + filtrage LLM…')

      const r = await fetch(`${BACKEND_URL}/scrape-job`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_url: response.url, job_html: response.html }),
      })

      if (!r.ok) {
        const txt = await r.text()
        throw new Error(`Backend ${r.status}: ${txt}`)
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
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setError('Copie impossible')
      setState('error')
    }
  }

  function handleReset() {
    setState('idle')
    setResult(null)
    setError('')
    setStep('')
  }

  const styles = {
    container: {
      padding: 20,
      width: 420,
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      color: '#1a1a2e',
    } as React.CSSProperties,
    title: { fontSize: 18, fontWeight: 700 } as React.CSSProperties,
    button: {
      flex: 1,
      padding: 12,
      backgroundColor: '#4f46e5',
      color: 'white',
      border: 'none',
      borderRadius: 8,
      fontSize: 15,
      fontWeight: 600,
      cursor: 'pointer',
    } as React.CSSProperties,
    field: {
      borderBottom: '1px solid #f3f4f6',
      padding: '8px 0',
      fontSize: 13,
      display: 'flex',
      flexDirection: 'column' as const,
      gap: 2,
    } as React.CSSProperties,
    label: {
      color: '#6b7280',
      fontSize: 11,
      textTransform: 'uppercase' as const,
      letterSpacing: 0.5,
    } as React.CSSProperties,
    value: { color: '#1f2937', wordBreak: 'break-word' as const } as React.CSSProperties,
    chips: { display: 'flex', flexWrap: 'wrap' as const, gap: 6, marginTop: 4 } as React.CSSProperties,
    chip: {
      padding: '3px 9px',
      borderRadius: 12,
      background: '#eef2ff',
      color: '#4f46e5',
      fontSize: 12,
      fontWeight: 500,
    } as React.CSSProperties,
    list: { margin: '4px 0 0', paddingLeft: 18, color: '#374151', fontSize: 13 } as React.CSSProperties,
    summary: {
      padding: 10,
      background: '#f9fafb',
      borderRadius: 6,
      fontSize: 13,
      lineHeight: 1.5,
      color: '#374151',
      marginTop: 4,
    } as React.CSSProperties,
    description: {
      maxHeight: 140,
      overflowY: 'auto' as const,
      padding: 10,
      background: '#f9fafb',
      borderRadius: 6,
      fontSize: 12,
      lineHeight: 1.5,
      color: '#6b7280',
      marginTop: 4,
    } as React.CSSProperties,
    badge: {
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 12,
      fontSize: 11,
      fontWeight: 600,
    } as React.CSSProperties,
    error: { color: '#dc2626', fontSize: 13, padding: 12, background: '#fef2f2', borderRadius: 6 } as React.CSSProperties,
  }

  if (state === 'idle') {
    return (
      <div style={styles.container}>
        <div style={styles.title}>Job Apply — Scraping</div>
        <p style={{ color: '#6b7280', fontSize: 13, margin: '8px 0 16px' }}>
          Analyse l'offre courante et affiche les infos essentielles.
        </p>
        <div style={{ display: 'flex' }}>
          <button style={styles.button} onClick={handleAnalyze}>
            Analyser cette offre
          </button>
        </div>
      </div>
    )
  }

  if (state === 'loading') {
    return (
      <div style={styles.container}>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        <div style={styles.title}>Job Apply — Scraping</div>
        <div style={{ textAlign: 'center', padding: 20 }}>
          <div
            style={{
              width: 36,
              height: 36,
              border: '4px solid #e5e7eb',
              borderTop: '4px solid #4f46e5',
              borderRadius: '50%',
              animation: 'spin 0.8s linear infinite',
              margin: '0 auto 12px',
            }}
          />
          <div style={{ color: '#6b7280', fontSize: 13 }}>{step}</div>
        </div>
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div style={styles.container}>
        <div style={styles.title}>Job Apply — Scraping</div>
        <div style={{ ...styles.error, marginTop: 12 }}>{error}</div>
        <div style={{ display: 'flex', marginTop: 12 }}>
          <button style={{ ...styles.button, backgroundColor: '#6b7280' }} onClick={handleReset}>
            Réessayer
          </button>
        </div>
      </div>
    )
  }

  // result
  const r = result ?? {}
  const fields: Array<[string, unknown]> = [
    ['Titre', r.title],
    ['Entreprise', r.company],
    ['Lieu', r.location],
    ['Type de contrat', r.contract_type ?? r.employment_type],
    ['Salaire', r.salary],
    ['Télétravail', fmtRemote(r.remote)],
    ['Expérience', r.experience_level],
    ['Date de publication', r.posted_date],
    ['Expire le', r.valid_through],
  ]
  const skills = Array.isArray(r.skills) ? r.skills : []
  const missions = Array.isArray(r.missions) ? r.missions : []

  return (
    <div style={styles.container}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={styles.title}>Job Apply — Scraping</div>
        <span
          style={{
            ...styles.badge,
            background: r.llm_used ? '#dcfce7' : '#fef9c3',
            color: r.llm_used ? '#15803d' : '#a16207',
          }}
        >
          {r.llm_used ? '✨ LLM' : 'brut'}
        </span>
      </div>

      {fields.map(([label, value]) =>
        value ? (
          <div key={label} style={styles.field}>
            <span style={styles.label}>{label}</span>
            <span style={styles.value}>{String(value)}</span>
          </div>
        ) : null,
      )}

      {skills.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <span style={styles.label}>Compétences</span>
          <div style={styles.chips}>
            {skills.map((s, i) => (
              <span key={i} style={styles.chip}>
                {String(s)}
              </span>
            ))}
          </div>
        </div>
      )}

      {missions.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <span style={styles.label}>Missions</span>
          <ul style={styles.list}>
            {missions.map((m, i) => (
              <li key={i}>{String(m)}</li>
            ))}
          </ul>
        </div>
      )}

      {r.summary && (
        <div style={{ marginTop: 10 }}>
          <span style={styles.label}>Résumé</span>
          <div style={styles.summary}>{r.summary}</div>
        </div>
      )}

      {r.description && (
        <div style={{ marginTop: 10 }}>
          <span style={styles.label}>Description brute</span>
          <div style={styles.description}>{r.description}</div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
        <button style={styles.button} onClick={handleCopy}>
          {copied ? '✅ Copié' : '📋 Copier JSON'}
        </button>
        <button style={{ ...styles.button, backgroundColor: '#6b7280' }} onClick={handleReset}>
          Reset
        </button>
      </div>
    </div>
  )
}
