/**
 * Background service worker — exécute les trois pipelines (scrape / tailor /
 * fill) en tâche de fond, hors du cycle de vie du popup. Le popup ne fait
 * qu'envoyer un message de déclenchement et s'abonne ensuite à
 * chrome.storage.local : le worker y écrit le statut + le résultat, et le
 * popup réagit aux onChanged. Conséquence : si tu fermes le popup pendant
 * une analyse, le fetch continue côté worker et le résultat apparaîtra à
 * la prochaine ouverture.
 *
 * Messages écoutés :
 *  - START_ANALYZE   { tabId }
 *  - START_TAILOR_CV { offer }
 *  - START_FILL_FORM { tabId, context? }
 *  - RESET_STATE
 *
 * Schéma de chrome.storage.local[STORAGE_KEY] :
 *  { status, result, error, applyError, fillReport,
 *    cvState, cvResult, cvError, inflight: { kind, started_at } | null }
 */

const BACKEND_URL = 'http://localhost:8000'
const STORAGE_KEY = 'job-apply-popup-state'

type Status =
  | 'idle' | 'scraping' | 'ready' | 'applying' | 'applied' | 'error' | 'apply-error'

type CvState = 'idle' | 'generating' | 'done' | 'error'

interface State {
  status: Status
  result: Record<string, unknown> | null
  error: string
  applyError: string
  fillReport: { filled: string[]; skipped: { id: string; reason: string }[] } | null
  cvState: CvState
  cvResult: Record<string, unknown> | null
  cvError: string
  inflight: { kind: 'scrape' | 'apply' | 'tailor'; started_at: number } | null
}

async function getState(): Promise<Partial<State>> {
  const data = await chrome.storage.local.get(STORAGE_KEY)
  return (data[STORAGE_KEY] as Partial<State>) ?? {}
}

async function setState(patch: Partial<State>): Promise<void> {
  const cur = await getState()
  await chrome.storage.local.set({ [STORAGE_KEY]: { ...cur, ...patch } })
}

function sendToTab<T = unknown>(tabId: number, message: unknown): Promise<T> {
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

function friendlyFetchError(err: unknown): string {
  if (err instanceof TypeError && /fetch|network/i.test(err.message)) {
    return 'Backend injoignable sur localhost:8000. Lance .\\dev.ps1 puis réessaie.'
  }
  return err instanceof Error ? err.message : 'Erreur inconnue'
}

// ──────────────────────────────────────────────────────────────────────────
// Pipelines
// ──────────────────────────────────────────────────────────────────────────

async function runAnalyze(tabId: number): Promise<void> {
  await setState({
    status: 'scraping',
    error: '',
    inflight: { kind: 'scrape', started_at: Date.now() },
  })
  try {
    const captured = await sendToTab<{ html: string; url: string }>(tabId, {
      type: 'CAPTURE_JOB_HTML',
    })
    if (!captured?.html) throw new Error('Impossible de capturer le HTML')
    const r = await fetch(`${BACKEND_URL}/scrape-job`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_url: captured.url, job_html: captured.html }),
    })
    if (!r.ok) {
      const txt = await r.text()
      throw new Error(`Backend ${r.status} — ${txt.slice(0, 140)}`)
    }
    const data = await r.json()
    await setState({ status: 'ready', result: data, error: '', inflight: null })
  } catch (err) {
    await setState({
      status: 'error',
      error: friendlyFetchError(err),
      inflight: null,
    })
  }
}

async function runTailorCv(offer: Record<string, unknown>): Promise<void> {
  await setState({
    cvState: 'generating',
    cvError: '',
    inflight: { kind: 'tailor', started_at: Date.now() },
  })
  try {
    const res = await fetch(`${BACKEND_URL}/tailor-cv`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ offer }),
    })
    if (!res.ok) {
      const txt = await res.text()
      throw new Error(`Backend ${res.status} — ${txt.slice(0, 180)}`)
    }
    const data = await res.json()
    await setState({ cvState: 'done', cvResult: data, cvError: '', inflight: null })
  } catch (err) {
    await setState({
      cvState: 'error',
      cvError: friendlyFetchError(err),
      inflight: null,
    })
  }
}

async function runFillForm(
  tabId: number,
  context: Record<string, unknown> | null,
): Promise<void> {
  await setState({
    status: 'applying',
    applyError: '',
    fillReport: null,
    inflight: { kind: 'apply', started_at: Date.now() },
  })
  try {
    const detected = await sendToTab<{ schema: { fields: unknown[] } | null }>(tabId, {
      type: 'DETECT_FORM',
    })
    const schema = detected?.schema
    if (!schema || !schema.fields || schema.fields.length === 0) {
      throw new Error('Aucun formulaire détecté sur cette page')
    }
    const res = await fetch(`${BACKEND_URL}/fill-form`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ form_schema: schema, context: context ?? {} }),
    })
    if (!res.ok) {
      const txt = await res.text()
      throw new Error(`Backend ${res.status} — ${txt.slice(0, 160)}`)
    }
    const fillPayload = await res.json()
    const report = await sendToTab<{
      filled: string[]
      skipped: { id: string; reason: string }[]
    }>(tabId, { type: 'FILL_FORM', payload: fillPayload })
    await setState({
      status: 'applied',
      fillReport: report,
      applyError: '',
      inflight: null,
    })
  } catch (err) {
    await setState({
      status: 'apply-error',
      applyError: friendlyFetchError(err),
      inflight: null,
    })
  }
}

async function resetState(): Promise<void> {
  await chrome.storage.local.remove(STORAGE_KEY)
}

// ──────────────────────────────────────────────────────────────────────────
// Message dispatcher
// ──────────────────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message !== 'object') return false
  switch (message.type) {
    case 'START_ANALYZE':
      runAnalyze(message.tabId).catch(() => {})
      sendResponse({ ok: true })
      return false
    case 'START_TAILOR_CV':
      runTailorCv(message.offer).catch(() => {})
      sendResponse({ ok: true })
      return false
    case 'START_FILL_FORM':
      runFillForm(message.tabId, message.context).catch(() => {})
      sendResponse({ ok: true })
      return false
    case 'RESET_STATE':
      resetState().then(() => sendResponse({ ok: true }))
      return true
  }
  return false
})

chrome.runtime.onInstalled.addListener(() => {
  console.log('Job Apply Agent installé')
})
