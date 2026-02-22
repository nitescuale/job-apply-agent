/**
 * Background service worker — gère les événements d'installation.
 */

chrome.runtime.onInstalled.addListener(() => {
  console.log('Job Apply Agent installé')
})
