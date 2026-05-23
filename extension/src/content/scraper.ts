/**
 * Content script — capture le HTML rendu de l'offre d'emploi.
 * Activé sur les sites supportés (manifest.json).
 *
 * Le HTML rendu (post-JS) est envoyé tel quel au backend qui le parse
 * via Scrapling (CSS selectors + JSON-LD JobPosting).
 */

const MAX_HTML_SIZE = 1_500_000 // ~1.5 MB, suffisant pour la plupart des pages d'offres

/**
 * Capture le HTML complet de la page après rendu JavaScript.
 * Tronque à MAX_HTML_SIZE pour éviter d'envoyer des pages géantes au backend.
 */
export function captureRenderedHtml(): string {
  const html = document.documentElement.outerHTML
  return html.length > MAX_HTML_SIZE ? html.slice(0, MAX_HTML_SIZE) : html
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === 'CAPTURE_JOB_HTML') {
    sendResponse({
      html: captureRenderedHtml(),
      url: window.location.href,
    })
  }
  return true
})
