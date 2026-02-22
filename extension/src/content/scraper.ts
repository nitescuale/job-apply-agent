/**
 * Content script — extrait le texte visible d'une offre d'emploi.
 * Activé sur les sites supportés définis dans manifest.json.
 */

/**
 * Extrait le texte visible pertinent de la page courante.
 * Supprime la navigation, les pieds de page, les scripts et styles.
 * Limite à 8000 caractères pour l'API.
 */
export function extractJobText(): string {
  const body = document.body.cloneNode(true) as HTMLElement

  // Supprimer les éléments non pertinents
  const selectorsToRemove = [
    'script', 'style', 'noscript',
    'nav', 'footer', 'header',
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    '.cookie-banner', '.ad', '.advertisement',
  ]
  selectorsToRemove.forEach(selector => {
    body.querySelectorAll(selector).forEach(el => el.remove())
  })

  // Extraire le texte, normaliser les espaces
  const text = (body.textContent ?? '')
    .replace(/\s+/g, ' ')
    .trim()

  return text.slice(0, 8000)
}

// Écouter les messages depuis le popup
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === 'EXTRACT_JOB_TEXT') {
    sendResponse({ text: extractJobText() })
  }
  return true // Garder le canal ouvert pour la réponse async
})
