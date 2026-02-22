import React, { useState } from 'react'

const BACKEND_URL = 'http://localhost:8000'

type State = 'idle' | 'loading' | 'result' | 'error'

interface AdaptedCV {
  title?: string
  summary?: string
  match_score?: number
  [key: string]: unknown
}

interface AnalysisResult {
  job_data: Record<string, unknown>
  adapted_cv: AdaptedCV
  match_score: number
}

export default function Popup() {
  const [state, setState] = useState<State>('idle')
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [error, setError] = useState<string>('')
  const [step, setStep] = useState<string>('')

  async function handleAnalyze() {
    setState('loading')
    setStep('Extraction du texte de la page...')

    try {
      // Récupérer l'onglet actif
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      if (!tab.id) throw new Error('Onglet non trouvé')

      setStep('Analyse de l\'offre en cours...')

      // Envoyer un message au content script pour extraire le texte
      const response = await chrome.tabs.sendMessage(tab.id, { type: 'EXTRACT_JOB_TEXT' }) as { text: string }
      const jobText = response?.text

      if (!jobText) throw new Error('Impossible d\'extraire le texte de la page')

      setStep('Adaptation du CV en cours...')

      // Appel backend
      const backendResponse = await fetch(`${BACKEND_URL}/analyze-and-adapt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_url: tab.url ?? '',
          job_text: jobText,
        }),
      })

      if (!backendResponse.ok) {
        const errText = await backendResponse.text()
        throw new Error(`Backend error ${backendResponse.status}: ${errText}`)
      }

      const data: AnalysisResult = await backendResponse.json()
      setResult(data)
      setState('result')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur inconnue')
      setState('error')
    }
  }

  function handleCopy() {
    if (!result) return
    navigator.clipboard.writeText(JSON.stringify(result.adapted_cv, null, 2))
  }

  function handleReset() {
    setState('idle')
    setResult(null)
    setError('')
    setStep('')
  }

  // Styles inline pour éviter les dépendances CSS externes
  const styles = {
    container: {
      padding: '20px',
      minWidth: '380px',
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    } as React.CSSProperties,
    title: {
      fontSize: '18px',
      fontWeight: 700,
      marginBottom: '16px',
      color: '#1a1a2e',
    } as React.CSSProperties,
    button: {
      width: '100%',
      padding: '12px',
      backgroundColor: '#4f46e5',
      color: 'white',
      border: 'none',
      borderRadius: '8px',
      fontSize: '15px',
      fontWeight: 600,
      cursor: 'pointer',
    } as React.CSSProperties,
    score: {
      fontSize: '24px',
      fontWeight: 700,
      color: '#4f46e5',
      textAlign: 'center' as const,
      margin: '12px 0',
    } as React.CSSProperties,
    summary: {
      fontSize: '13px',
      lineHeight: '1.6',
      color: '#374151',
      backgroundColor: '#f9fafb',
      padding: '12px',
      borderRadius: '6px',
      marginBottom: '12px',
    } as React.CSSProperties,
    error: {
      color: '#dc2626',
      fontSize: '13px',
      padding: '12px',
      backgroundColor: '#fef2f2',
      borderRadius: '6px',
    } as React.CSSProperties,
    stepText: {
      textAlign: 'center' as const,
      color: '#6b7280',
      fontSize: '13px',
      marginTop: '12px',
    } as React.CSSProperties,
  }

  if (state === 'idle') {
    return (
      <div style={styles.container}>
        <div style={styles.title}>Job Apply Agent</div>
        <p style={{ color: '#6b7280', fontSize: '13px', marginBottom: '16px' }}>
          Analysez cette offre et obtenez un CV adapté automatiquement.
        </p>
        <button style={styles.button} onClick={handleAnalyze}>
          Analyser cette offre
        </button>
      </div>
    )
  }

  if (state === 'loading') {
    return (
      <div style={styles.container}>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
        <div style={styles.title}>Job Apply Agent</div>
        <div style={{ textAlign: 'center', padding: '20px' }}>
          <div style={{
            width: '40px',
            height: '40px',
            border: '4px solid #e5e7eb',
            borderTop: '4px solid #4f46e5',
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite',
            margin: '0 auto 12px',
          }} />
          <div style={styles.stepText}>{step}</div>
        </div>
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div style={styles.container}>
        <div style={styles.title}>Job Apply Agent</div>
        <div style={styles.error}>{error}</div>
        <button style={{ ...styles.button, marginTop: '12px', backgroundColor: '#6b7280' }} onClick={handleReset}>
          Réessayer
        </button>
      </div>
    )
  }

  // state === 'result'
  const score = result ? Math.round((result.match_score ?? 0) * 100) : 0
  const summary = result?.adapted_cv?.summary as string | undefined

  return (
    <div style={styles.container}>
      <div style={styles.title}>Job Apply Agent</div>
      <div style={styles.score}>
        Match : {score}%
      </div>
      {summary && (
        <div style={styles.summary}>
          <strong>Résumé adapté :</strong><br />
          {summary}
        </div>
      )}
      <button style={styles.button} onClick={handleCopy}>
        Copier le CV
      </button>
      <button
        style={{ ...styles.button, marginTop: '8px', backgroundColor: '#6b7280' }}
        onClick={handleReset}
      >
        Nouvelle analyse
      </button>
    </div>
  )
}
