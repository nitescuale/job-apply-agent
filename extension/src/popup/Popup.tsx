import React, { useEffect, useState } from 'react'
import {
  APPLICATION_STATUSES,
  STATUS_LABELS,
  isProgressedStatus,
  type ApplicationStatus,
} from '../shared/status'

const BACKEND_URL = 'http://localhost:8000'
const STORAGE_KEY = 'job-apply-popup-state'
const STALE_INFLIGHT_MS = 90_000 // au-delà, on considère que le worker a sauté

const IS_MAC =
  typeof navigator !== 'undefined' && /mac/i.test(navigator.platform || navigator.userAgent)
const SHORTCUT_HINT = IS_MAC ? '⌘ ↵ pour postuler' : 'Ctrl ↵ pour postuler'
const SHORTCUT_KEYCAP = IS_MAC ? '⌘↵' : 'Ctrl ↵'

type Status =
  | 'idle'        // before any analysis — direct entry to all 3 actions
  | 'scraping'    // analysing the page
  | 'ready'       // result panel shown, ready to apply
  | 'applying'    // auto-apply in flight (with or without offer context)
  | 'applied'     // form filled successfully
  | 'error'       // analysis error
  | 'apply-error' // apply error (keep showing whatever was shown before)

interface MatchResult {
  score: number
  matched_skills: string[]
  missing_skills: string[]
  rationale: string
  llm_used: boolean
}

interface OfferResult {
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
  description?: string
  match_score?: number
  source?: string
  skills?: string[]
  missions?: string[]
  summary?: string
  llm_used?: boolean
  llm_error?: string
  // tracking — alimentés par le store SQLite côté backend
  application_id?: number
  seen_before?: boolean
  application_status?: string
  from_cache?: boolean
  // score de pertinence (alimenté par /match-score automatiquement après scrape)
  match?: MatchResult | null
  [key: string]: unknown
}

interface FillReport {
  filled: string[]
  skipped: { id: string; reason: string }[]
}

interface AtsCheck {
  name: string
  passed: boolean
  detail: string
  weight?: number
  partial_score?: number
}

interface AtsReport {
  ats_score: number
  checks: AtsCheck[]
  suggestions: string[]
  matched_skills?: string[]
  missing_skills?: string[]
  page_count?: number
  text_length?: number
}

interface CvResult {
  saved_path: string
  filename: string
  folder: string
  markdown?: string
  summary_used?: boolean
  ats?: AtsReport
}

interface CoverLetterResult {
  saved_path: string
  filename: string
  folder: string
  text?: string
}

type CvState = 'idle' | 'generating' | 'done' | 'error'
type ClState = 'idle' | 'generating' | 'done' | 'error'

// ──────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────

function formatRemote(v: boolean | string | undefined): string | null {
  if (v === undefined || v === null) return null
  if (typeof v === 'boolean') return v ? 'Oui' : 'Non'
  return String(v).trim() || null
}

function formatFrenchDate(input?: string): string | null {
  if (!input) return null
  const d = new Date(input)
  if (Number.isNaN(d.getTime())) return input
  return d
    .toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' })
    .replace('.', '.')
}

function firstInitial(s?: string): string {
  return (s ?? '').trim().slice(0, 1).toUpperCase() || '·'
}

const STYLES = `
  :root {
    --bg: #f7f7f5;
    --pan: #ffffff;
    --ink: #23241f;
    --mut: #82837b;
    --faint: #b4b5ac;
    --line: #e9e9e3;
    --ac: #3d7d5a;
    --ac-soft: #e7f1eb;
    --bad: #b3503e;
    --bad-soft: #f6e7e2;
    --sans: 'Hanken Grotesk', system-ui, sans-serif;
    --mono: 'Spline Sans Mono', ui-monospace, monospace;
  }

  .ja-panel {
    width: 100%;
    height: 100%;
    background: var(--bg);
    color: var(--ink);
    font-family: var(--sans);
    font-size: 14px;
    line-height: 1.5;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* top bar */
  .ja-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 18px;
    border-bottom: 1px solid var(--line);
    background: var(--pan);
    flex-shrink: 0;
  }
  .ja-brand {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: -0.01em;
  }
  .ja-mark {
    width: 18px;
    height: 18px;
    border-radius: 6px;
    background: var(--ink);
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--sans);
  }
  .ja-kbd {
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 3px 7px;
    background: var(--bg);
    white-space: nowrap;
  }
  .ja-bar-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .ja-bar-reset {
    width: 26px;
    height: 26px;
    border: 1px solid var(--line);
    border-radius: 7px;
    background: var(--bg);
    color: var(--mut);
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
  }
  .ja-bar-reset:hover {
    background: var(--ac-soft);
    border-color: var(--ac);
    color: var(--ac);
  }
  .ja-bar-tracker {
    height: 26px;
    border: 1px solid var(--line);
    border-radius: 7px;
    background: var(--bg);
    color: var(--mut);
    font-family: var(--mono);
    font-size: 10.5px;
    letter-spacing: 0.02em;
    padding: 0 9px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 5px;
    transition: background 0.15s, color 0.15s, border-color 0.15s;
  }
  .ja-bar-tracker:hover {
    background: var(--ac-soft);
    border-color: var(--ac);
    color: var(--ac);
  }

  /* body */
  .ja-body {
    padding: 22px 22px 0;
    overflow-y: auto;
    overflow-x: hidden;
    flex: 1 1 0;            /* flex-basis 0 + flex-grow 1 → prend la place
                               restante et rien d'autre. */
    min-height: 0;          /* requis : sinon en flex column les enfants
                               refusent de shrink sous leur contenu et
                               le scroll ne s'active jamais. */
  }
  .ja-tag {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.04em;
    color: var(--ac);
    background: var(--ac-soft);
    padding: 5px 10px;
    border-radius: 7px;
    margin-bottom: 16px;
  }
  .ja-tag.muted {
    color: var(--mut);
    background: #f0f0ea;
  }
  .ja-badge-status {
    display: inline-flex;
    align-items: center;
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.04em;
    padding: 5px 10px;
    border-radius: 7px;
    margin-bottom: 16px;
    margin-left: 8px;
    border: 1px solid transparent;
  }
  .ja-badge-status.seen {
    color: var(--mut);
    background: var(--pan);
    border-color: var(--line);
  }
  .ja-badge-status.progressed {
    color: var(--ac);
    background: var(--ac-soft);
    border-color: transparent;
  }
  .ja-status-pick {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 16px;
    margin-left: 8px;
  }
  .ja-status-pick label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.04em;
    color: var(--mut);
    text-transform: lowercase;
  }
  .ja-status-pick select {
    font-family: var(--mono);
    font-size: 10.5px;
    letter-spacing: 0.02em;
    padding: 4px 22px 4px 8px;
    border: 1px solid var(--line);
    border-radius: 7px;
    background: var(--pan);
    color: var(--ink);
    cursor: pointer;
    appearance: none;
    -webkit-appearance: none;
    background-image:
      linear-gradient(45deg, transparent 50%, var(--mut) 50%),
      linear-gradient(135deg, var(--mut) 50%, transparent 50%);
    background-position: calc(100% - 11px) 50%, calc(100% - 7px) 50%;
    background-size: 4px 4px;
    background-repeat: no-repeat;
    transition: border-color 0.15s, background-color 0.15s;
  }
  .ja-status-pick select:hover { border-color: var(--ac); }
  .ja-status-pick select.progressed {
    background-color: var(--ac-soft);
    color: var(--ac);
    border-color: transparent;
  }
  .ja-status-pick select:disabled { opacity: 0.6; cursor: default; }

  /* match score card */
  .ja-match {
    border: 1px solid var(--line);
    border-radius: 13px;
    background: var(--pan);
    padding: 14px 15px;
    margin: 0 0 18px;
  }
  .ja-match-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }
  .ja-match-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.06em;
    color: var(--mut);
    text-transform: lowercase;
  }
  .ja-match-mode {
    font-family: var(--mono);
    font-size: 9.5px;
    color: var(--faint);
    letter-spacing: 0.04em;
  }
  .ja-match-row {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 8px;
  }
  .ja-match-num {
    font-size: 30px;
    font-weight: 700;
    letter-spacing: -0.03em;
    line-height: 1;
    color: var(--ac);
    font-variant-numeric: tabular-nums;
  }
  .ja-match-num.weak { color: var(--bad); }
  .ja-match-num.mid { color: var(--warn, #b88a3f); }
  .ja-match-bar {
    flex: 1;
    height: 4px;
    background: var(--line);
    border-radius: 3px;
    overflow: hidden;
  }
  .ja-match-bar-fill {
    height: 100%;
    background: var(--ac);
    transition: width 0.3s ease;
  }
  .ja-match-bar-fill.weak { background: var(--bad); }
  .ja-match-bar-fill.mid { background: var(--warn, #b88a3f); }
  .ja-match-rationale {
    font-size: 12.5px;
    line-height: 1.5;
    color: #54564d;
    margin: 0 0 10px;
  }
  .ja-match-missing-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.04em;
    color: var(--mut);
    text-transform: lowercase;
    margin-bottom: 6px;
  }
  .ja-match-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }
  .ja-match-chip {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.02em;
    padding: 3px 8px;
    border-radius: 6px;
    background: var(--bad-soft);
    color: var(--bad);
    border: 1px solid transparent;
  }
  .ja-match-chip.matched {
    background: var(--ac-soft);
    color: var(--ac);
  }
  .ja-match-pending {
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  /* ats badge + panel */
  .ja-ats-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
  }
  .ja-ats-badge {
    font-family: var(--mono);
    font-size: 10.5px;
    letter-spacing: 0.02em;
    padding: 3px 9px;
    border-radius: 7px;
    background: var(--ac-soft);
    color: var(--ac);
    border: 1px solid transparent;
    font-variant-numeric: tabular-nums;
  }
  .ja-ats-badge.weak { background: var(--bad-soft); color: var(--bad); }
  .ja-ats-badge.mid { background: #f5ebd9; color: #b88a3f; }
  .ja-ats-toggle {
    background: none;
    border: none;
    padding: 0;
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    cursor: pointer;
    text-decoration: underline;
    text-decoration-color: var(--line);
    text-underline-offset: 3px;
  }
  .ja-ats-toggle:hover { color: var(--ink); text-decoration-color: var(--mut); }
  .ja-ats-panel {
    margin-top: 8px;
    border: 1px solid var(--line);
    border-radius: 9px;
    background: var(--bg);
    padding: 10px 12px;
  }
  .ja-ats-section-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.04em;
    color: var(--mut);
    text-transform: lowercase;
    margin: 0 0 6px;
  }
  .ja-ats-list {
    list-style: none;
    margin: 0;
    padding: 0;
    font-size: 12px;
    line-height: 1.5;
    color: #54564d;
  }
  .ja-ats-list li {
    display: flex;
    gap: 8px;
    margin-bottom: 4px;
  }
  .ja-ats-list li::before {
    content: '·';
    color: var(--faint);
    flex-shrink: 0;
  }
  .ja-ats-checks {
    margin-top: 10px;
  }
  .ja-ats-check {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 11.5px;
    line-height: 1.5;
    color: var(--mut);
    margin-bottom: 3px;
  }
  .ja-ats-mark {
    font-family: var(--mono);
    font-size: 11px;
    width: 14px;
    flex-shrink: 0;
    text-align: center;
  }
  .ja-ats-mark.ok { color: var(--ac); }
  .ja-ats-mark.ko { color: var(--bad); }
  .ja-company {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-bottom: 9px;
  }
  .ja-logo {
    width: 26px;
    height: 26px;
    border-radius: 7px;
    background: #eceae2;
    border: 1px solid var(--line);
    font-size: 11px;
    font-weight: 700;
    color: #6c6d63;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }
  .ja-company-name {
    font-size: 13px;
    color: var(--mut);
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ja-h1 {
    margin: 0 0 18px;
    font-size: 22px;
    line-height: 1.22;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--ink);
  }

  /* metadata table */
  .ja-rows {
    border: 1px solid var(--line);
    border-radius: 13px;
    overflow: hidden;
    background: var(--pan);
    margin: 0 0 18px;
  }
  .ja-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 11px 15px;
    border-bottom: 1px solid var(--line);
  }
  .ja-row:last-child { border-bottom: none; }
  .ja-dt {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--mut);
    letter-spacing: 0.02em;
  }
  .ja-dd {
    margin: 0;
    font-size: 13.5px;
    font-weight: 600;
    color: var(--ink);
    text-align: right;
    max-width: 60%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .ja-dd.empty {
    color: var(--faint);
    font-weight: 500;
  }

  .ja-desc {
    color: #54564d;
    font-size: 13px;
    line-height: 1.6;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
    margin: 0 0 22px;
  }
  .ja-desc.expanded {
    display: block;
    -webkit-line-clamp: unset;
    -webkit-box-orient: unset;
  }
  .ja-desc-more {
    background: none;
    border: none;
    padding: 0;
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    cursor: pointer;
    letter-spacing: 0.02em;
    margin-bottom: 18px;
  }
  .ja-desc-more:hover { color: var(--ink); }

  /* footer */
  .ja-foot {
    padding: 14px 18px 18px;
    border-top: 1px solid var(--line);
    background: var(--pan);
    display: flex;
    gap: 10px;
    align-items: center;
    flex-direction: column;
    flex-shrink: 0;
  }
  .ja-foot-row {
    display: flex;
    gap: 10px;
    align-items: center;
    width: 100%;
  }
  .ja-cta {
    flex: 1;
    height: 46px;
    border: none;
    border-radius: 11px;
    background: var(--ink);
    color: #fff;
    font-family: var(--sans);
    font-size: 13.5px;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    transition: opacity 0.15s, transform 0.1s, background 0.15s;
  }
  .ja-cta:hover { opacity: 0.9; }
  .ja-cta:active { transform: translateY(1px); }
  .ja-cta:disabled { cursor: default; opacity: 0.65; }
  .ja-cta.success {
    background: var(--ac);
  }
  .ja-cta.ja-cta-secondary {
    background: var(--pan);
    color: var(--ink);
    border: 1px solid var(--line);
  }
  .ja-cta.ja-cta-secondary:hover {
    background: #efefe9;
    opacity: 1;
  }
  .ja-keycap {
    font-family: var(--mono);
    font-size: 10px;
    opacity: 0.6;
    border: 1px solid rgba(255, 255, 255, 0.25);
    border-radius: 5px;
    padding: 2px 5px;
  }
  .ja-keycap.dark {
    color: var(--mut);
    border-color: var(--line);
    background: var(--bg);
    opacity: 1;
  }
  .ja-icon {
    width: 46px;
    height: 46px;
    border-radius: 11px;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--mut);
    font-size: 16px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.15s, color 0.15s;
    flex-shrink: 0;
  }
  .ja-icon:hover { background: #efefe9; color: var(--ink); }
  .ja-icon:disabled { cursor: default; opacity: 0.5; }

  .ja-status-line {
    width: 100%;
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    letter-spacing: 0.02em;
    text-align: center;
  }
  .ja-status-line .ok { color: var(--ac); }
  .ja-status-line .ko { color: var(--bad); }
  .ja-status-line .reset {
    background: none;
    border: none;
    color: var(--mut);
    font-family: var(--mono);
    font-size: 10.5px;
    cursor: pointer;
    text-decoration: underline;
    text-decoration-color: var(--line);
    text-underline-offset: 3px;
    padding: 0;
    margin-left: 6px;
  }
  .ja-status-line .reset:hover { color: var(--ink); text-decoration-color: var(--mut); }

  /* idle */
  .ja-idle {
    padding: 60px 22px 40px;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
  .ja-idle h1 {
    margin: 0;
    font-size: 26px;
    line-height: 1.18;
    font-weight: 700;
    letter-spacing: -0.025em;
    color: var(--ink);
  }
  .ja-idle p {
    margin: 0 0 16px;
    font-size: 13.5px;
    color: var(--mut);
    line-height: 1.55;
  }
  .ja-idle .ja-cta { width: 100%; flex: none; }

  /* loading */
  .ja-loading {
    padding: 56px 22px 40px;
  }
  .ja-loading-msg {
    font-size: 18px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ink);
    margin-bottom: 22px;
  }
  .ja-bar-scan {
    height: 2px;
    background: var(--line);
    border-radius: 2px;
    overflow: hidden;
    position: relative;
  }
  .ja-bar-scan::after {
    content: '';
    position: absolute;
    inset: 0;
    width: 40%;
    background: linear-gradient(90deg, transparent, var(--ac), transparent);
    animation: ja-scan 1.6s ease-in-out infinite;
  }
  @keyframes ja-scan {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(350%); }
  }

  /* error */
  .ja-error {
    padding: 56px 22px 40px;
  }
  .ja-err-label {
    font-family: var(--mono);
    font-size: 10.5px;
    letter-spacing: 0.04em;
    color: var(--bad);
    margin-bottom: 12px;
  }
  .ja-err-msg {
    font-size: 18px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ink);
    margin-bottom: 12px;
  }
  .ja-err-detail {
    font-family: var(--mono);
    font-size: 11px;
    line-height: 1.5;
    color: var(--mut);
    background: var(--pan);
    border: 1px solid var(--line);
    border-radius: 11px;
    padding: 12px 14px;
    margin-bottom: 18px;
    word-break: break-word;
  }

  /* cv tailor card */
  .ja-cv {
    border: 1px solid var(--line);
    border-radius: 13px;
    background: var(--pan);
    padding: 14px 15px;
    margin: 0 0 18px;
  }
  .ja-cv-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.06em;
    color: var(--mut);
    text-transform: lowercase;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .ja-cv-btn {
    width: 100%;
    height: 38px;
    border-radius: 9px;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--ink);
    font-family: var(--sans);
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    transition: background 0.15s, border-color 0.15s;
  }
  .ja-cv-btn:hover {
    background: var(--ac-soft);
    border-color: var(--ac);
    color: var(--ac);
  }
  .ja-cv-btn:disabled { cursor: default; opacity: 0.6; }
  .ja-cv-btn.success {
    background: var(--ac-soft);
    border-color: var(--ac);
    color: var(--ac);
  }
  .ja-cv-file {
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    margin: 8px 0 0;
    word-break: break-all;
    line-height: 1.5;
  }
  .ja-cv-regen {
    background: none;
    border: none;
    padding: 0;
    margin: 8px 0 0;
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--mut);
    cursor: pointer;
    text-decoration: underline;
    text-decoration-color: var(--line);
    text-underline-offset: 3px;
  }
  .ja-cv-regen:hover {
    color: var(--ink);
    text-decoration-color: var(--mut);
  }
  .ja-cv-error {
    font-family: var(--mono);
    font-size: 10.5px;
    color: var(--bad);
    line-height: 1.5;
    word-break: break-word;
  }
  .ja-spin-dark {
    width: 12px;
    height: 12px;
    border: 2px solid var(--line);
    border-top-color: var(--ink);
    border-radius: 50%;
    animation: ja-spin 0.8s linear infinite;
  }

  /* spinner inside CTA */
  .ja-spin {
    width: 14px;
    height: 14px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: ja-spin 0.8s linear infinite;
  }
  @keyframes ja-spin {
    to { transform: rotate(360deg); }
  }
`

// ──────────────────────────────────────────────────────────────────────────
// Sub-components
// ──────────────────────────────────────────────────────────────────────────

function TopBar({
  showShortcut,
  onReset,
  onOpenTracker,
}: {
  showShortcut: boolean
  onReset?: () => void
  onOpenTracker?: () => void
}) {
  return (
    <div className="ja-bar">
      <div className="ja-brand">
        <span className="ja-mark">J</span>
        Job Apply
      </div>
      <div className="ja-bar-right">
        {showShortcut && <span className="ja-kbd">{SHORTCUT_HINT}</span>}
        {onOpenTracker && (
          <button
            type="button"
            className="ja-bar-tracker"
            onClick={onOpenTracker}
            aria-label="Suivi des candidatures"
            title="Suivi des candidatures"
          >
            ▤ Suivi
          </button>
        )}
        {onReset && (
          <button
            type="button"
            className="ja-bar-reset"
            onClick={onReset}
            aria-label="Réinitialiser"
            title="Réinitialiser (nouvelle offre)"
          >
            ↺
          </button>
        )}
      </div>
    </div>
  )
}


function Row({ label, value }: { label: string; value: string | null }) {
  const empty = value === null || value === ''
  return (
    <div className="ja-row">
      <dt className="ja-dt">{label}</dt>
      <dd className={`ja-dd${empty ? ' empty' : ''}`}>{empty ? 'non précisé' : value}</dd>
    </div>
  )
}

function scoreVariant(score: number): '' | 'mid' | 'weak' {
  if (score >= 70) return ''
  if (score >= 45) return 'mid'
  return 'weak'
}

const ATS_CHECK_LABELS: Record<string, string> = {
  parsability: 'texte extractible',
  keyword_coverage: 'couverture des compétences',
  section_experience: 'section expérience',
  section_education: 'section formation',
  section_skills: 'section compétences',
  length: 'longueur 1-2 pages',
  contact_block: 'bloc contact',
}

function AtsBadge({ ats }: { ats: AtsReport }) {
  const [open, setOpen] = useState(false)
  const variant = scoreVariant(ats.ats_score)
  const hasContent =
    ats.suggestions.length > 0 || ats.checks.length > 0
  return (
    <>
      <div className="ja-ats-row">
        <span className={`ja-ats-badge ${variant}`} title="Score ATS déterministe">
          ATS · {ats.ats_score}/100
        </span>
        {hasContent && (
          <button
            type="button"
            className="ja-ats-toggle"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
          >
            {open ? 'masquer le détail' : 'voir le détail'}
          </button>
        )}
      </div>
      {open && (
        <div className="ja-ats-panel">
          {ats.suggestions.length > 0 && (
            <>
              <p className="ja-ats-section-label">
                suggestions ({ats.suggestions.length})
              </p>
              <ul className="ja-ats-list">
                {ats.suggestions.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </>
          )}
          <div className="ja-ats-checks">
            <p className="ja-ats-section-label">checks</p>
            {ats.checks.map((c) => (
              <div key={c.name} className="ja-ats-check">
                <span className={`ja-ats-mark ${c.passed ? 'ok' : 'ko'}`}>
                  {c.passed ? '✓' : '✕'}
                </span>
                <span>
                  <strong style={{ color: 'var(--ink)' }}>
                    {ATS_CHECK_LABELS[c.name] || c.name}
                  </strong>
                  {' — '}
                  {c.detail}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

function MatchCard({ match }: { match: MatchResult | null | undefined }) {
  // match=undefined → en cours de calcul (background a fini scrape, fetch
  // match-score en vol). match=null ou objet → on rend la carte. Pas de
  // carte si l'utilisateur n'est pas sur "ready" avec un scrape complet.
  if (match === undefined) {
    return (
      <div className="ja-match">
        <div className="ja-match-head">
          <span className="ja-match-label">score de pertinence</span>
        </div>
        <div className="ja-match-pending">
          <span className="ja-spin-dark" /> Calcul du score…
        </div>
      </div>
    )
  }
  if (!match) return null
  const variant = scoreVariant(match.score)
  const missing = match.missing_skills ?? []
  return (
    <div className="ja-match">
      <div className="ja-match-head">
        <span className="ja-match-label">score de pertinence</span>
        <span className="ja-match-mode">
          {match.llm_used ? 'gemini' : 'hors-ligne'}
        </span>
      </div>
      <div className="ja-match-row">
        <span className={`ja-match-num ${variant}`}>{match.score}</span>
        <div className="ja-match-bar">
          <div
            className={`ja-match-bar-fill ${variant}`}
            style={{ width: `${Math.max(0, Math.min(100, match.score))}%` }}
          />
        </div>
      </div>
      {match.rationale && <p className="ja-match-rationale">{match.rationale}</p>}
      {missing.length > 0 && (
        <>
          <div className="ja-match-missing-label">
            compétences manquantes ({missing.length})
          </div>
          <div className="ja-match-chips">
            {missing.slice(0, 12).map((s) => (
              <span key={s} className="ja-match-chip">
                {s}
              </span>
            ))}
            {missing.length > 12 && (
              <span className="ja-match-chip">+{missing.length - 12}</span>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Popup
// ──────────────────────────────────────────────────────────────────────────

export default function Popup() {
  const [status, setStatus] = useState<Status>('idle')
  const [result, setResult] = useState<OfferResult | null>(null)
  const [error, setError] = useState('')
  const [applyError, setApplyError] = useState('')
  const [fillReport, setFillReport] = useState<FillReport | null>(null)
  const [descExpanded, setDescExpanded] = useState(false)
  const [cvState, setCvState] = useState<CvState>('idle')
  const [cvResult, setCvResult] = useState<CvResult | null>(null)
  const [cvError, setCvError] = useState('')
  const [clState, setClState] = useState<ClState>('idle')
  const [clResult, setClResult] = useState<CoverLetterResult | null>(null)
  const [clError, setClError] = useState('')
  // Le popup MV3 est détruit dès qu'on clique en dehors. Le boulot (fetch
  // backend, content-script round-trips) est délégué au service worker
  // (background.ts) qui survit aux fermetures, écrit son avancement dans
  // chrome.storage.local, et nous notifie via onChanged. Le popup ne fait
  // qu'hydrater + écouter — il ne possède plus l'état.
  function applySnapshot(saved: Partial<{
    status: Status
    result: OfferResult | null
    error: string
    applyError: string
    fillReport: FillReport | null
    cvState: CvState
    cvResult: CvResult | null
    cvError: string
    clState: ClState
    clResult: CoverLetterResult | null
    clError: string
    inflight: { kind: 'scrape' | 'apply' | 'tailor' | 'cover'; started_at: number } | null
  }>) {
    const inflight = saved.inflight
    const isStale = !!inflight && Date.now() - inflight.started_at > STALE_INFLIGHT_MS
    let s: Status = saved.status ?? 'idle'
    let cv: CvState = saved.cvState ?? 'idle'
    let cl: ClState = saved.clState ?? 'idle'
    let err = saved.error ?? ''
    let applyErr = saved.applyError ?? ''
    let cvErr = saved.cvError ?? ''
    let clErr = saved.clError ?? ''
    if (isStale) {
      // Le worker a probablement été tué (browser closed, very long idle)
      // sans terminer son fetch. On rabat sur un état d'erreur explicite.
      if (s === 'scraping') {
        s = 'error'
        err = err || 'Analyse interrompue. Réessaie.'
      }
      if (s === 'applying') {
        s = 'apply-error'
        applyErr = applyErr || 'Remplissage interrompu. Réessaie.'
      }
      if (cv === 'generating') {
        cv = 'error'
        cvErr = cvErr || 'Génération interrompue. Réessaie.'
      }
      if (cl === 'generating') {
        cl = 'error'
        clErr = clErr || 'Génération interrompue. Réessaie.'
      }
    }
    setStatus(s)
    setResult(saved.result ?? null)
    setError(err)
    setApplyError(applyErr)
    setFillReport(saved.fillReport ?? null)
    setCvState(cv)
    setCvResult(saved.cvResult ?? null)
    setCvError(cvErr)
    setClState(cl)
    setClResult(saved.clResult ?? null)
    setClError(clErr)
  }

  // Hydrate au mount + écoute live des écritures background
  useEffect(() => {
    if (typeof chrome === 'undefined' || !chrome.storage?.local) return
    chrome.storage.local.get(STORAGE_KEY, (data) => {
      const saved = data[STORAGE_KEY]
      if (saved) applySnapshot(saved)
    })
    const onChanged = (
      changes: { [key: string]: chrome.storage.StorageChange },
      area: string,
    ) => {
      if (area !== 'local') return
      const c = changes[STORAGE_KEY]
      if (!c) return
      if (c.newValue) applySnapshot(c.newValue)
      else {
        // RESET_STATE a supprimé la clé entière → retour à idle
        setStatus('idle')
        setResult(null)
        setError('')
        setApplyError('')
        setFillReport(null)
        setCvState('idle')
        setCvResult(null)
        setCvError('')
        setClState('idle')
        setClResult(null)
        setClError('')
      }
    }
    chrome.storage.onChanged.addListener(onChanged)
    return () => chrome.storage.onChanged.removeListener(onChanged)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleAnalyze() {
    setDescExpanded(false)
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    if (!tab.id) {
      setError('Onglet non trouvé')
      setStatus('error')
      return
    }
    // Le background tient le pipeline (capture + fetch + écriture storage).
    // On ne touche pas à l'état local — onChanged va le pousser dès que le
    // worker écrit 'scraping' puis 'ready'/'error'.
    chrome.runtime.sendMessage({ type: 'START_ANALYZE', tabId: tab.id })
  }

  async function handleApply() {
    if (status === 'applying') return
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    if (!tab.id) {
      setApplyError('Onglet non trouvé')
      setStatus('apply-error')
      return
    }
    // Si l'offre n'a pas été analysée au préalable, on envoie un contexte
    // vide — Gemini se base alors uniquement sur le profil pour remplir.
    const context = result
      ? { title: result.title, company: result.company, location: result.location }
      : {}
    chrome.runtime.sendMessage({ type: 'START_FILL_FORM', tabId: tab.id, context })
  }

  function handleOpenOriginal() {
    if (result?.url) {
      chrome.tabs.create({ url: result.url })
    }
  }

  async function handleTailorCv() {
    if (!result || cvState === 'generating') return
    const offer = {
      title: result.title,
      company: result.company,
      location: result.location,
      contract_type: result.contract_type ?? result.employment_type,
      salary: result.salary,
      remote: result.remote,
      experience_level: result.experience_level,
      skills: result.skills,
      missions: result.missions,
      summary: result.summary,
      description: result.description,
      url: result.url,
    }
    chrome.runtime.sendMessage({ type: 'START_TAILOR_CV', offer })
  }

  async function handleOpenCv() {
    if (!cvResult?.saved_path) return
    // Chrome MV3 et Firefox bloquent silencieusement chrome.tabs.create avec
    // un file:// (sauf option "Autoriser l'accès aux URL de fichier" activée
    // manuellement). On route via le backend qui ouvre le PDF dans le lecteur
    // par défaut de l'OS (os.startfile / xdg-open / open) — UX identique sur
    // Chrome et Firefox sans demander de droit spécial.
    try {
      const res = await fetch(`${BACKEND_URL}/open-file`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: cvResult.saved_path }),
      })
      if (!res.ok) {
        const txt = await res.text()
        throw new Error(`Backend ${res.status} — ${txt.slice(0, 140)}`)
      }
    } catch (err) {
      setCvError(err instanceof Error ? err.message : 'Erreur inconnue')
      setCvState('error')
    }
  }

  function handleOpenTracker() {
    chrome.runtime.sendMessage({ type: 'OPEN_TRACKER' })
  }

  function handleStatusChange(newStatus: ApplicationStatus) {
    const appId = result?.application_id
    if (typeof appId !== 'number') return
    // Optimistic update — le background va PATCH puis confirmer via storage,
    // mais on flippe l'UI immédiatement pour la responsivité.
    setResult({ ...(result as OfferResult), application_status: newStatus, seen_before: true })
    chrome.runtime.sendMessage({
      type: 'PATCH_APPLICATION',
      applicationId: appId,
      patch: { status: newStatus },
    })
  }

  function handleReset() {
    // Local immédiat + signal au background qui wipe storage. onChanged
    // ré-appliquera l'état vide, ce qui est idempotent côté React.
    setStatus('idle')
    setResult(null)
    setError('')
    setApplyError('')
    setFillReport(null)
    setDescExpanded(false)
    setCvState('idle')
    setCvResult(null)
    setCvError('')
    setClState('idle')
    setClResult(null)
    setClError('')
    chrome.runtime.sendMessage({ type: 'RESET_STATE' })
  }

  async function handleCoverLetter() {
    if (!result || clState === 'generating') return
    const offer = {
      title: result.title,
      company: result.company,
      location: result.location,
      contract_type: result.contract_type ?? result.employment_type,
      salary: result.salary,
      remote: result.remote,
      experience_level: result.experience_level,
      skills: result.skills,
      missions: result.missions,
      summary: result.summary,
      description: result.description,
      url: result.url,
    }
    chrome.runtime.sendMessage({ type: 'START_COVER_LETTER', offer })
  }

  async function handleOpenCoverLetter() {
    if (!clResult?.saved_path) return
    try {
      const res = await fetch(`${BACKEND_URL}/open-file`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: clResult.saved_path }),
      })
      if (!res.ok) {
        const txt = await res.text()
        throw new Error(`Backend ${res.status} — ${txt.slice(0, 140)}`)
      }
    } catch (err) {
      setClError(err instanceof Error ? err.message : 'Erreur inconnue')
      setClState('error')
    }
  }

  // Cmd+Enter / Ctrl+Enter triggers Postuler dès qu'on peut remplir un form
  // (idle = direct entry, ready = après analyse, apply-error = retry)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        if (status === 'idle' || status === 'ready' || status === 'apply-error') {
          e.preventDefault()
          handleApply()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, result])

  // ── Render ─────────────────────────────────────────────────────────────

  if (status === 'idle') {
    return (
      <>
        <style>{STYLES}</style>
        <div className="ja-panel">
          <TopBar showShortcut={false} onOpenTracker={handleOpenTracker} />
          <div className="ja-idle">
            <h1>Page d'offre ou de candidature ?</h1>
            <p>
              Si tu es sur l'offre, analyse-la d'abord. Si tu es déjà sur le
              formulaire, lance directement le remplissage.
            </p>
            <button className="ja-cta" onClick={handleAnalyze}>
              Analyser la page
            </button>
            <button className="ja-cta ja-cta-secondary" onClick={handleApply}>
              Remplir le formulaire <span className="ja-keycap dark">{SHORTCUT_KEYCAP}</span>
            </button>
          </div>
        </div>
      </>
    )
  }

  // Cas "remplir sans analyse" — applying / applied / apply-error sans result
  if (!result && (status === 'applying' || status === 'applied' || status === 'apply-error')) {
    return (
      <>
        <style>{STYLES}</style>
        <div className="ja-panel">
          <TopBar showShortcut={false} onReset={handleReset} onOpenTracker={handleOpenTracker} />
          <div className="ja-idle">
            <h1>Remplissage du formulaire.</h1>
            {status === 'applying' && (
              <>
                <p>Détection des champs et mapping via Gemini…</p>
                <div className="ja-bar-scan" style={{ width: '100%' }} />
              </>
            )}
            {status === 'applied' && fillReport && (
              <>
                <p>
                  <span style={{ color: 'var(--ac)', fontWeight: 600 }}>
                    {fillReport.filled.length} champ{fillReport.filled.length > 1 ? 's' : ''} rempli{fillReport.filled.length > 1 ? 's' : ''}
                  </span>
                  {fillReport.skipped.length > 0 && (
                    <>
                      {' · '}
                      <span style={{ color: 'var(--bad)' }}>
                        {fillReport.skipped.length} ignoré{fillReport.skipped.length > 1 ? 's' : ''}
                      </span>
                    </>
                  )}
                  . Vérifie les champs surlignés en ambre et soumets toi-même.
                </p>
                <button className="ja-cta" onClick={handleReset}>
                  Terminer
                </button>
              </>
            )}
            {status === 'apply-error' && (
              <>
                <p style={{ color: 'var(--bad)' }}>{applyError}</p>
                <button className="ja-cta" onClick={handleApply}>
                  Réessayer
                </button>
                <button className="ja-cta ja-cta-secondary" onClick={handleReset}>
                  Retour
                </button>
              </>
            )}
          </div>
        </div>
      </>
    )
  }

  if (status === 'scraping') {
    return (
      <>
        <style>{STYLES}</style>
        <div className="ja-panel">
          <TopBar showShortcut={false} onReset={handleReset} onOpenTracker={handleOpenTracker} />
          <div className="ja-loading">
            <div className="ja-loading-msg">Extraction + filtrage LLM…</div>
            <div className="ja-bar-scan" />
          </div>
        </div>
      </>
    )
  }

  if (status === 'error') {
    return (
      <>
        <style>{STYLES}</style>
        <div className="ja-panel">
          <TopBar showShortcut={false} onReset={handleReset} onOpenTracker={handleOpenTracker} />
          <div className="ja-error">
            <div className="ja-err-label">erreur · analyse</div>
            <div className="ja-err-msg">Échec de l'analyse.</div>
            <div className="ja-err-detail">{error}</div>
            <button className="ja-cta" onClick={handleReset}>
              Réessayer
            </button>
          </div>
        </div>
      </>
    )
  }

  // status ∈ { ready, applying, applied, apply-error }
  const r = result ?? {}
  const contract = r.contract_type ?? r.employment_type ?? null
  const remote = formatRemote(r.remote)
  const exp = r.experience_level ?? null
  const published = formatFrenchDate(r.posted_date)
  const expires = formatFrenchDate(r.valid_through)
  const matchScore =
    typeof r.match_score === 'number' && Number.isFinite(r.match_score)
      ? Math.round(r.match_score)
      : null

  const companyLine = [r.company, r.location].filter(Boolean).join(' · ')

  const isApplying = status === 'applying'
  const isApplied = status === 'applied'
  const isApplyError = status === 'apply-error'

  return (
    <>
      <style>{STYLES}</style>
      <div className="ja-panel">
        <TopBar
          showShortcut={status === 'ready'}
          onReset={handleReset}
          onOpenTracker={handleOpenTracker}
        />

        <div className="ja-body">
          {r.llm_used ? (
            <span className="ja-tag">
              ✦ filtré par LLM
              {matchScore !== null && <> · {matchScore}% match</>}
            </span>
          ) : (
            <span className="ja-tag muted">scraping brut</span>
          )}
          {typeof r.application_id === 'number' && (
            <span className="ja-status-pick">
              <label htmlFor="ja-status-select">statut</label>
              <select
                id="ja-status-select"
                value={r.application_status ?? 'seen'}
                className={isProgressedStatus(r.application_status) ? 'progressed' : ''}
                onChange={(e) => handleStatusChange(e.target.value as ApplicationStatus)}
                title="Changer le statut de cette candidature"
              >
                {APPLICATION_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {STATUS_LABELS[s]}
                  </option>
                ))}
              </select>
            </span>
          )}

          {(r.company || r.location) && (
            <div className="ja-company">
              <span className="ja-logo">{firstInitial(r.company)}</span>
              <span className="ja-company-name">{companyLine || '—'}</span>
            </div>
          )}

          <h1 className="ja-h1">{r.title || 'Sans titre'}</h1>

          <dl className="ja-rows">
            <Row label="contrat" value={contract} />
            <Row label="salaire" value={r.salary ?? null} />
            <Row label="télétravail" value={remote} />
            <Row label="expérience" value={exp} />
            <Row label="publié" value={published} />
            {expires && <Row label="expire" value={expires} />}
          </dl>

          <MatchCard match={r.match} />

          <div className="ja-cv">
            <div className="ja-cv-label">
              <span>cv · adapter pour ce poste</span>
              {cvState === 'done' && <span style={{ color: 'var(--ac)' }}>généré</span>}
            </div>

            {(cvState === 'idle' || cvState === 'error') && (
              <button className="ja-cv-btn" onClick={handleTailorCv}>
                Adapter le CV
              </button>
            )}

            {cvState === 'generating' && (
              <button className="ja-cv-btn" disabled>
                <span className="ja-spin-dark" /> Génération…
              </button>
            )}

            {cvState === 'done' && cvResult && (
              <>
                <button className="ja-cv-btn success" onClick={handleOpenCv}>
                  ✓ Ouvrir le PDF
                </button>
                <div className="ja-cv-file">{cvResult.filename}</div>
                {cvResult.ats && <AtsBadge ats={cvResult.ats} />}
                <button className="ja-cv-regen" onClick={handleTailorCv}>
                  ↻ régénérer
                </button>
              </>
            )}

            {cvState === 'error' && (
              <div className="ja-cv-error" style={{ marginTop: 8 }}>
                {cvError}
              </div>
            )}
          </div>

          <div className="ja-cv">
            <div className="ja-cv-label">
              <span>lettre · long-form personnalisée</span>
              {clState === 'done' && <span style={{ color: 'var(--ac)' }}>générée</span>}
            </div>

            {(clState === 'idle' || clState === 'error') && (
              <button className="ja-cv-btn" onClick={handleCoverLetter}>
                Lettre de motivation
              </button>
            )}

            {clState === 'generating' && (
              <button className="ja-cv-btn" disabled>
                <span className="ja-spin-dark" /> Génération…
              </button>
            )}

            {clState === 'done' && clResult && (
              <>
                <button className="ja-cv-btn success" onClick={handleOpenCoverLetter}>
                  ✓ Ouvrir le PDF
                </button>
                <div className="ja-cv-file">{clResult.filename}</div>
                <button className="ja-cv-regen" onClick={handleCoverLetter}>
                  ↻ régénérer
                </button>
              </>
            )}

            {clState === 'error' && (
              <div className="ja-cv-error" style={{ marginTop: 8 }}>
                {clError}
              </div>
            )}
          </div>

          {r.description && (
            <>
              <p className={`ja-desc${descExpanded ? ' expanded' : ''}`}>{r.description}</p>
              {r.description.length > 180 && (
                <button
                  className="ja-desc-more"
                  onClick={() => setDescExpanded((v) => !v)}
                >
                  {descExpanded ? '— Réduire' : '+ Lire la suite'}
                </button>
              )}
            </>
          )}
        </div>

        <div className="ja-foot">
          <div className="ja-foot-row">
            <button
              className={`ja-cta${isApplied ? ' success' : ''}`}
              onClick={handleApply}
              disabled={isApplying || isApplied}
            >
              {isApplying && (
                <>
                  <span className="ja-spin" /> Envoi…
                </>
              )}
              {isApplied && <>✓ Formulaire rempli</>}
              {(status === 'ready' || isApplyError) && (
                <>
                  Postuler <span className="ja-keycap">{SHORTCUT_KEYCAP}</span>
                </>
              )}
            </button>
            <button
              className="ja-icon"
              onClick={handleOpenOriginal}
              disabled={!r.url}
              aria-label="Voir l'offre d'origine"
              title="Voir l'offre d'origine"
            >
              ↗
            </button>
          </div>

          {isApplied && fillReport && (
            <div className="ja-status-line">
              <span className="ok">{fillReport.filled.length} rempli{fillReport.filled.length > 1 ? 's' : ''}</span>
              {fillReport.skipped.length > 0 && (
                <>
                  {' · '}
                  <span className="ko">
                    {fillReport.skipped.length} ignoré{fillReport.skipped.length > 1 ? 's' : ''}
                  </span>
                </>
              )}
              <button className="reset" onClick={handleReset}>
                nouvelle analyse
              </button>
            </div>
          )}

          {isApplyError && (
            <div className="ja-status-line">
              <span className="ko">{applyError}</span>
              <button className="reset" onClick={handleReset}>
                réinitialiser
              </button>
            </div>
          )}

          {status === 'ready' && (
            <div className="ja-status-line">
              <button className="reset" onClick={handleReset}>
                nouvelle analyse
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
