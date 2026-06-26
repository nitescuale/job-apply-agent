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
 *  - START_ANALYZE       { tabId }
 *  - START_TAILOR_CV     { offer }
 *  - START_COVER_LETTER  { offer }
 *  - START_FILL_FORM     { tabId, context? }
 *  - PATCH_APPLICATION   { applicationId, patch: { status?, notes? } }
 *  - OPEN_TRACKER        — ouvre tracker.html dans un nouvel onglet
 *  - RESET_STATE
 *
 * Schéma de chrome.storage.local[STORAGE_KEY] :
 *  { status, result, error, applyError, fillReport,
 *    cvState, cvResult, cvError,
 *    clState, clResult, clError,           // cover letter
 *    inflight: { kind, started_at } | null }
 */

const BACKEND_URL = 'http://localhost:8000'
const STORAGE_KEY = 'job-apply-popup-state'
const TRACKER_URL = 'src/tracker/index.html'

type Status =
  | 'idle' | 'scraping' | 'ready' | 'applying' | 'applied' | 'error' | 'apply-error'

type CvState = 'idle' | 'generating' | 'done' | 'error'
type ClState = 'idle' | 'generating' | 'done' | 'error'

interface MatchResult {
  score: number
  matched_skills: string[]
  missing_skills: string[]
  rationale: string
  llm_used: boolean
}

interface OfferResult {
  application_id?: number
  application_status?: string
  seen_before?: boolean
  match?: MatchResult | null
  [key: string]: unknown
}

interface State {
  status: Status
  result: OfferResult | null
  error: string
  applyError: string
  fillReport: { filled: string[]; skipped: { id: string; reason: string }[] } | null
  cvState: CvState
  cvResult: Record<string, unknown> | null
  cvError: string
  clState: ClState
  clResult: Record<string, unknown> | null
  clError: string
  inflight: { kind: 'scrape' | 'apply' | 'tailor' | 'cover'; started_at: number } | null
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
// Tracking — PATCH /applications/{id}
// ──────────────────────────────────────────────────────────────────────────

interface ApplicationRow {
  id: number
  status: string
  notes?: string | null
  [key: string]: unknown
}

async function patchApplication(
  applicationId: number,
  patch: { status?: string; notes?: string },
): Promise<ApplicationRow> {
  const res = await fetch(`${BACKEND_URL}/applications/${applicationId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) {
    const txt = await res.text()
    throw new Error(`Backend ${res.status} — ${txt.slice(0, 180)}`)
  }
  return res.json()
}

/**
 * Si le storage courant reflète cette même application, met à jour
 * `result.application_status` pour que le badge du popup change live (via
 * onChanged). Si l'application affichée est différente — on no-op.
 */
async function reflectStatusInStorage(applicationId: number, status: string): Promise<void> {
  const cur = await getState()
  const r = cur.result
  if (!r || r.application_id !== applicationId) return
  await setState({
    result: { ...r, application_status: status, seen_before: true },
  })
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

    // Étape 2 (en parallèle conceptuel — sériel pour rester simple) :
    // score de pertinence. On l'attache à `result.match` pour que le popup
    // l'affiche dès qu'il arrive. Erreur silencieuse en console — la jauge
    // reste cachée plutôt que de planter l'analyse principale.
    //
    // IMPORTANT : on settle TOUJOURS `match` à un score OU à `null` (jamais
    // laisser `undefined`). Sinon la popup montre le placeholder "Calcul du
    // score…" indéfiniment quand le backend renvoie non-OK (504, 502) ou
    // que le fetch throw. Avec `null` la MatchCard masque proprement.
    let match: MatchResult | null = null
    try {
      const offerForMatch = {
        title: data.title,
        company: data.company,
        location: data.location,
        contract_type: data.contract_type,
        experience_level: data.experience_level,
        skills: data.skills,
        missions: data.missions,
        summary: data.summary,
      }
      const mr = await fetch(`${BACKEND_URL}/match-score`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ offer: offerForMatch }),
      })
      if (mr.ok) {
        match = (await mr.json()) as MatchResult
      } else {
        console.warn('match-score auto-trigger non-OK:', mr.status)
      }
    } catch (err) {
      console.warn('match-score auto-trigger a échoué :', err)
    }
    // Settle dans tous les cas (score | null) — la popup arrête le spinner.
    const curAfter = await getState()
    const curResultAfter = curAfter.result
    if (curResultAfter) {
      await setState({ result: { ...curResultAfter, match } })
    }
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
    // Si on a déjà un match attaché à l'offre courante (storage), on
    // le forward au backend pour orienter le tailoring sans inventer
    // les missing_skills. Rétrocompat : si match est absent, le backend
    // construit le prompt comme avant.
    const cur = await getState()
    const match = cur.result?.match ?? undefined
    const res = await fetch(`${BACKEND_URL}/tailor-cv`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(match ? { offer, match } : { offer }),
    })
    if (!res.ok) {
      const txt = await res.text()
      throw new Error(`Backend ${res.status} — ${txt.slice(0, 180)}`)
    }
    const data = await res.json()
    await setState({ cvState: 'done', cvResult: data, cvError: '', inflight: null })

    // ATS lint déterministe sur le PDF qu'on vient de générer. Erreur
    // silencieuse en console — un échec ici n'invalide pas le CV, on
    // perd juste le badge. Le report est attaché à cvResult.ats pour que
    // la popup le rende sans recharger.
    const pdfPath = (data as { saved_path?: unknown })?.saved_path
    if (typeof pdfPath === 'string' && pdfPath) {
      try {
        const lintRes = await fetch(`${BACKEND_URL}/ats-lint`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pdf_path: pdfPath, offer }),
        })
        if (lintRes.ok) {
          const ats = await lintRes.json()
          const cur = await getState()
          const curCv = cur.cvResult
          if (curCv) {
            await setState({ cvResult: { ...curCv, ats } })
          }
        }
      } catch (err) {
        console.warn('ats-lint auto-trigger a échoué :', err)
      }
    }
  } catch (err) {
    await setState({
      cvState: 'error',
      cvError: friendlyFetchError(err),
      inflight: null,
    })
  }
}

async function runCoverLetter(offer: Record<string, unknown>): Promise<void> {
  await setState({
    clState: 'generating',
    clError: '',
    inflight: { kind: 'cover', started_at: Date.now() },
  })
  try {
    const cur = await getState()
    const match = cur.result?.match ?? undefined
    const res = await fetch(`${BACKEND_URL}/cover-letter`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(match ? { offer, match } : { offer }),
    })
    if (!res.ok) {
      const txt = await res.text()
      throw new Error(`Backend ${res.status} — ${txt.slice(0, 180)}`)
    }
    const data = await res.json()
    await setState({ clState: 'done', clResult: data, clError: '', inflight: null })
  } catch (err) {
    await setState({
      clState: 'error',
      clError: friendlyFetchError(err),
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

    // Auto-PATCH status='applied' sur l'application liée — quand on a un
    // application_id sous la main (parce que l'utilisateur a scrapé l'offre
    // avant de remplir). Si fill-form a été appelé sans scraping préalable,
    // il n'y a pas encore d'application en DB ; l'utilisateur pourra
    // ajouter le statut manuellement depuis le tracker.
    const cur = await getState()
    const appId = cur.result?.application_id
    if (typeof appId === 'number') {
      try {
        const row = await patchApplication(appId, { status: 'applied' })
        await reflectStatusInStorage(appId, row.status)
      } catch (err) {
        // Pas bloquant — le formulaire est bien rempli, seul le tracking
        // a raté. On log mais on ne marque pas l'état comme erreur.
        console.warn('Auto-PATCH applied a échoué :', err)
      }
    }
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

async function openTracker(): Promise<void> {
  await chrome.tabs.create({ url: chrome.runtime.getURL(TRACKER_URL) })
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
    case 'START_COVER_LETTER':
      runCoverLetter(message.offer).catch(() => {})
      sendResponse({ ok: true })
      return false
    case 'START_FILL_FORM':
      runFillForm(message.tabId, message.context).catch(() => {})
      sendResponse({ ok: true })
      return false
    case 'PATCH_APPLICATION':
      patchApplication(message.applicationId, message.patch)
        .then(async (row) => {
          await reflectStatusInStorage(message.applicationId, row.status)
          sendResponse({ ok: true, row })
        })
        .catch((err) => sendResponse({ ok: false, error: friendlyFetchError(err) }))
      return true // async response (sendResponse appelé après await)
    case 'OPEN_TRACKER':
      openTracker().then(() => sendResponse({ ok: true }))
      return true
    case 'RESET_STATE':
      resetState().then(() => sendResponse({ ok: true }))
      return true
  }
  return false
})

chrome.runtime.onInstalled.addListener(() => {
  console.log('Job Apply Agent installé')
})
