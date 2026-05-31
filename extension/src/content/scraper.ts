/**
 * Content script — capture le HTML de la page (analyse d'offre) +
 * détection / remplissage du formulaire de candidature.
 *
 * Messages écoutés :
 *  - CAPTURE_JOB_HTML : renvoie le HTML rendu pour l'analyse côté backend
 *  - DETECT_FORM      : renvoie un schéma JSON des champs du formulaire le plus pertinent
 *  - FILL_FORM        : prend un mapping {field_id: value} et remplit les champs
 *                       (React-safe + trick DataTransfer pour <input type=file>)
 */

const MAX_HTML_SIZE = 1_500_000

// ──────────────────────────────────────────────────────────────────────────
// Capture HTML (existant)
// ──────────────────────────────────────────────────────────────────────────

function captureRenderedHtml(): string {
  const html = document.documentElement.outerHTML
  return html.length > MAX_HTML_SIZE ? html.slice(0, MAX_HTML_SIZE) : html
}

// ──────────────────────────────────────────────────────────────────────────
// Détection du formulaire
// ──────────────────────────────────────────────────────────────────────────

interface FormFieldSchema {
  id: string                 // selector unique (data-jp-id attribué à la volée)
  name?: string
  label: string
  type: string
  required: boolean
  placeholder?: string
  options?: string[]         // pour select/radio
  maxLength?: number
}

interface FormSchema {
  formSelector: string
  fields: FormFieldSchema[]
}

const FIELD_ID_ATTR = 'data-jp-field-id'

function visibleText(el: Element | null): string {
  if (!el) return ''
  const t = (el as HTMLElement).innerText || el.textContent || ''
  return t.replace(/\s+/g, ' ').trim()
}

function inferLabel(input: HTMLElement): string {
  const id = input.getAttribute('id')
  if (id) {
    const lbl = document.querySelector(`label[for="${CSS.escape(id)}"]`)
    if (lbl) return visibleText(lbl)
  }
  const parentLabel = input.closest('label')
  if (parentLabel) {
    const clone = parentLabel.cloneNode(true) as HTMLElement
    clone.querySelectorAll('input, select, textarea').forEach((n) => n.remove())
    const t = visibleText(clone)
    if (t) return t
  }
  const aria = input.getAttribute('aria-label')
  if (aria) return aria
  const ariaId = input.getAttribute('aria-labelledby')
  if (ariaId) {
    const ref = document.getElementById(ariaId)
    if (ref) return visibleText(ref)
  }
  const placeholder = (input as HTMLInputElement).placeholder
  if (placeholder) return placeholder
  const name = input.getAttribute('name')
  if (name) return name
  return ''
}

function pickBestForm(): HTMLFormElement | null {
  const forms = Array.from(document.querySelectorAll('form')) as HTMLFormElement[]
  if (forms.length === 0) {
    // Certains sites n'enveloppent pas les champs dans <form> (LinkedIn Easy Apply).
    // On considère alors le document entier comme conteneur si on trouve assez de inputs.
    const orphanInputs = document.querySelectorAll(
      'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select',
    )
    if (orphanInputs.length >= 3) return document.body as unknown as HTMLFormElement
    return null
  }
  // Le meilleur form = celui avec le plus d'inputs *visibles*
  let best: HTMLFormElement | null = null
  let bestScore = -1
  for (const f of forms) {
    const inputs = f.querySelectorAll(
      'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select',
    )
    if (inputs.length > bestScore) {
      best = f
      bestScore = inputs.length
    }
  }
  return best
}

function detectForm(): FormSchema | null {
  const form = pickBestForm()
  if (!form) return null

  const inputs = Array.from(
    form.querySelectorAll<HTMLElement>(
      'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select',
    ),
  )

  const fields: FormFieldSchema[] = []
  inputs.forEach((el, idx) => {
    const fieldId = el.getAttribute(FIELD_ID_ATTR) ?? `field_${idx}`
    el.setAttribute(FIELD_ID_ATTR, fieldId)

    const type = el.tagName === 'TEXTAREA'
      ? 'textarea'
      : el.tagName === 'SELECT'
      ? 'select'
      : (el as HTMLInputElement).type || 'text'

    if (type === 'radio') {
      // Pour les radios, on dédoublonne par name : un seul "champ" avec la liste d'options
      const name = el.getAttribute('name') ?? ''
      if (fields.some((f) => f.name === name && f.type === 'radio')) return
      const group = Array.from(
        form.querySelectorAll<HTMLInputElement>(`input[type=radio][name="${CSS.escape(name)}"]`),
      )
      const opts = group.map((g) => inferLabel(g) || g.value).filter(Boolean)
      const groupLabel =
        inferLabel(el) ||
        visibleText(el.closest('fieldset')?.querySelector('legend') ?? null) ||
        name
      fields.push({
        id: fieldId,
        name,
        label: groupLabel,
        type: 'radio',
        required: group.some((g) => g.required),
        options: opts,
      })
      return
    }

    const field: FormFieldSchema = {
      id: fieldId,
      name: el.getAttribute('name') ?? undefined,
      label: inferLabel(el),
      type,
      required: (el as HTMLInputElement).required ?? false,
    }

    const placeholder = (el as HTMLInputElement).placeholder
    if (placeholder) field.placeholder = placeholder

    const maxLength = (el as HTMLInputElement).maxLength
    if (maxLength && maxLength > 0) field.maxLength = maxLength

    if (type === 'select') {
      const opts = Array.from((el as HTMLSelectElement).options)
        .map((o) => o.text.trim())
        .filter((t) => t && !t.toLowerCase().startsWith('--'))
      field.options = opts
    }

    fields.push(field)
  })

  return {
    formSelector: form.tagName.toLowerCase(),
    fields,
  }
}

// ──────────────────────────────────────────────────────────────────────────
// Remplissage
// ──────────────────────────────────────────────────────────────────────────

/**
 * Setter "natif" pour outsmarter React/Vue qui interceptent .value et
 * remettent à zéro nos écritures. Sans ça, beaucoup de formulaires modernes
 * ignorent silencieusement le remplissage.
 */
function setNativeValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto = Object.getPrototypeOf(el)
  const desc = Object.getOwnPropertyDescriptor(proto, 'value')
  const setter = desc?.set
  if (setter) {
    setter.call(el, value)
  } else {
    el.value = value
  }
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}

function findFieldById(fieldId: string): HTMLElement | null {
  return document.querySelector(`[${FIELD_ID_ATTR}="${CSS.escape(fieldId)}"]`)
}

function highlight(el: HTMLElement) {
  const prev = el.style.outline
  const prevTransition = el.style.transition
  el.style.transition = 'outline 0.3s ease'
  el.style.outline = '2px solid #e8b07c'
  setTimeout(() => {
    el.style.outline = prev
    setTimeout(() => {
      el.style.transition = prevTransition
    }, 400)
  }, 1400)
}

/**
 * Trick DataTransfer : attribue un File programmatiquement à un <input type=file>.
 * Marche sur la plupart des sites modernes. Certains avec validation stricte
 * (isTrusted) refusent — pas de garantie absolue.
 */
function fillFileInput(input: HTMLInputElement, base64: string, filename = 'cv.pdf') {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))
  const file = new File([bytes], filename, { type: 'application/pdf' })
  const dt = new DataTransfer()
  dt.items.add(file)
  input.files = dt.files
  input.dispatchEvent(new Event('change', { bubbles: true }))
}

interface FillRequest {
  values: Record<string, string | boolean>
  cv_base64?: string | null
}

interface FillResult {
  filled: string[]
  skipped: { id: string; reason: string }[]
}

function fillForm({ values, cv_base64 }: FillRequest): FillResult {
  const filled: string[] = []
  const skipped: { id: string; reason: string }[] = []

  for (const [fieldId, value] of Object.entries(values ?? {})) {
    const el = findFieldById(fieldId)
    if (!el) {
      skipped.push({ id: fieldId, reason: 'not found' })
      continue
    }

    const tag = el.tagName
    const type = (el as HTMLInputElement).type

    try {
      if (tag === 'TEXTAREA' || (tag === 'INPUT' && !['file', 'checkbox', 'radio'].includes(type))) {
        setNativeValue(el as HTMLInputElement | HTMLTextAreaElement, String(value))
      } else if (tag === 'SELECT') {
        const sel = el as HTMLSelectElement
        const opt = Array.from(sel.options).find(
          (o) =>
            o.text.trim().toLowerCase() === String(value).toLowerCase() ||
            o.value.toLowerCase() === String(value).toLowerCase(),
        )
        if (opt) {
          sel.value = opt.value
          sel.dispatchEvent(new Event('change', { bubbles: true }))
        } else {
          skipped.push({ id: fieldId, reason: `option "${value}" introuvable` })
          continue
        }
      } else if (type === 'checkbox') {
        ;(el as HTMLInputElement).checked = Boolean(value)
        el.dispatchEvent(new Event('change', { bubbles: true }))
      } else if (type === 'radio') {
        // value = label de l'option choisie ; on retrouve le radio correspondant dans le groupe
        const name = el.getAttribute('name') ?? ''
        const group = Array.from(
          document.querySelectorAll<HTMLInputElement>(`input[type=radio][name="${CSS.escape(name)}"]`),
        )
        const target = group.find(
          (r) => inferLabel(r).trim().toLowerCase() === String(value).toLowerCase(),
        )
        if (target) {
          target.checked = true
          target.dispatchEvent(new Event('change', { bubbles: true }))
        } else {
          skipped.push({ id: fieldId, reason: `option radio "${value}" introuvable` })
          continue
        }
      } else if (type === 'file') {
        if (value === '__CV__' && cv_base64) {
          fillFileInput(el as HTMLInputElement, cv_base64)
        } else {
          skipped.push({ id: fieldId, reason: 'pas de CV configuré (cv_path vide)' })
          continue
        }
      } else {
        skipped.push({ id: fieldId, reason: `type non géré: ${type}` })
        continue
      }

      highlight(el)
      filled.push(fieldId)
    } catch (err) {
      skipped.push({ id: fieldId, reason: err instanceof Error ? err.message : 'erreur inconnue' })
    }
  }

  return { filled, skipped }
}

// ──────────────────────────────────────────────────────────────────────────
// Message handler
// ──────────────────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  try {
    if (message.type === 'CAPTURE_JOB_HTML') {
      sendResponse({ html: captureRenderedHtml(), url: window.location.href })
    } else if (message.type === 'DETECT_FORM') {
      sendResponse({ schema: detectForm() })
    } else if (message.type === 'FILL_FORM') {
      sendResponse(fillForm(message.payload as FillRequest))
    }
  } catch (err) {
    sendResponse({ error: err instanceof Error ? err.message : String(err) })
  }
  return true
})
