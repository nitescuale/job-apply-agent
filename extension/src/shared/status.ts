/**
 * Source of truth pour les statuts d'application — partagée entre popup et
 * tracker. Doit rester synchronisée avec backend/store.py `VALID_STATUSES`.
 */
export const APPLICATION_STATUSES = [
  'seen',
  'applied',
  'followed_up',
  'interview',
  'response_pos',
  'response_neg',
] as const

export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number]

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  seen: 'Déjà vu',
  applied: 'Déjà postulé',
  followed_up: 'Relancée',
  interview: 'Entretien',
  response_pos: 'Réponse positive',
  response_neg: 'Réponse négative',
}

/** True si le statut représente une candidature au-delà du simple "vu". */
export function isProgressedStatus(s: string | undefined): boolean {
  return !!s && s !== 'seen'
}
