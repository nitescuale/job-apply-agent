#!/usr/bin/env node
/**
 * Build Firefox-compatible distribution from the standard Vite/CRXJS output.
 *
 * Pipeline:
 *   1. Runs `vite build` inside `extension/` (CRXJS produces a Chrome MV3 dist).
 *   2. Copies `extension/dist/` → `extension/dist-firefox/`.
 *   3. Patches the manifest for Firefox MV3:
 *      - adds `browser_specific_settings.gecko.{id,strict_min_version}`
 *      - converts `background.service_worker` → `background.scripts` (Firefox
 *        MV3 does not implement service workers identically; persistent
 *        background scripts are the supported equivalent)
 *      - drops `background.type` (Firefox doesn't honor "module" on `scripts`)
 *   4. Validates the result by re-parsing.
 *
 * Load the resulting `dist-firefox/` in Firefox via about:debugging → "This
 * Firefox" → "Load Temporary Add-on" → pick any file inside dist-firefox.
 */
import { spawnSync } from 'node:child_process'
import { existsSync, rmSync, cpSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '..')
const EXT_DIR = join(REPO_ROOT, 'extension')
const CHROME_DIST = join(EXT_DIR, 'dist')
const FIREFOX_DIST = join(EXT_DIR, 'dist-firefox')

const GECKO_ID = 'job-apply-agent@nitescuale.dev'
const GECKO_MIN = '121.0'

function log(step, msg) {
  process.stdout.write(`[build:firefox] ${step}: ${msg}\n`)
}

function run(cmd, args, cwd) {
  const r = spawnSync(cmd, args, { cwd, stdio: 'inherit', shell: true })
  if (r.status !== 0) {
    process.stderr.write(`[build:firefox] command failed: ${cmd} ${args.join(' ')}\n`)
    process.exit(r.status ?? 1)
  }
}

// 1. Vite build (CRXJS → dist/)
log('1/4', 'running vite build')
if (existsSync(CHROME_DIST)) {
  rmSync(CHROME_DIST, { recursive: true, force: true })
}
run('npm', ['run', 'build'], EXT_DIR)

if (!existsSync(CHROME_DIST)) {
  process.stderr.write('[build:firefox] expected extension/dist after build but it is missing\n')
  process.exit(1)
}

// 2. Copy dist → dist-firefox
log('2/4', `copying dist → ${FIREFOX_DIST}`)
if (existsSync(FIREFOX_DIST)) {
  rmSync(FIREFOX_DIST, { recursive: true, force: true })
}
cpSync(CHROME_DIST, FIREFOX_DIST, { recursive: true })

// 3. Patch manifest
const manifestPath = join(FIREFOX_DIST, 'manifest.json')
if (!existsSync(manifestPath)) {
  process.stderr.write(`[build:firefox] manifest.json not found in ${FIREFOX_DIST}\n`)
  process.exit(1)
}
log('3/4', 'patching manifest for Firefox MV3')
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))

// browser_specific_settings — required for Firefox add-on identity.
// data_collection_permissions = "none" preempts a future-required key
// (Mozilla rolled it out as a manifest notice late 2025; declaring "none"
// means the add-on collects nothing — true for this extension, since all
// data stays on the user's machine and goes only to their own backend).
manifest.browser_specific_settings = {
  gecko: {
    id: GECKO_ID,
    strict_min_version: GECKO_MIN,
    data_collection_permissions: { required: ['none'] },
  },
}

// Convert background.service_worker → background.scripts (FF MV3 path).
// CRXJS emits:
//   "background": { "service_worker": "service-worker-loader.js", "type": "module" }
// where service-worker-loader.js is a tiny ES-module file: `import './assets/background.ts-XXX.js';`
// Firefox `background.scripts` loads classic scripts — module `import` syntax errors out.
// We resolve the import target and point `scripts` directly at the bundled file,
// then delete the now-unused loader to avoid confusion.
if (manifest.background?.service_worker) {
  const loaderRel = manifest.background.service_worker
  const loaderAbs = join(FIREFOX_DIST, loaderRel)
  let target = loaderRel
  if (existsSync(loaderAbs)) {
    const loaderSrc = readFileSync(loaderAbs, 'utf8')
    const m = loaderSrc.match(/import\s+['"]\.?\/?([^'"]+)['"]/)
    if (m) {
      target = m[1]
      rmSync(loaderAbs, { force: true })
    }
  }
  manifest.background = { scripts: [target] }
}

writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n', 'utf8')

// 4. Re-parse to make sure we didn't corrupt anything
log('4/4', 'validating output')
const reparsed = JSON.parse(readFileSync(manifestPath, 'utf8'))
const checks = [
  ['manifest_version', reparsed.manifest_version === 3],
  ['has gecko id', reparsed.browser_specific_settings?.gecko?.id === GECKO_ID],
  ['background.scripts is array', Array.isArray(reparsed.background?.scripts)],
  ['no service_worker leftover', !reparsed.background?.service_worker],
  ['host_permissions present', Array.isArray(reparsed.host_permissions)],
  ['content_scripts present', Array.isArray(reparsed.content_scripts)],
]
let ok = true
for (const [name, pass] of checks) {
  process.stdout.write(`           ${pass ? 'ok' : 'KO'} — ${name}\n`)
  if (!pass) ok = false
}
if (!ok) {
  process.stderr.write('[build:firefox] manifest validation failed\n')
  process.exit(1)
}

log('done', `Firefox build ready at ${FIREFOX_DIST}`)
log('load', 'Open about:debugging#/runtime/this-firefox → "Load Temporary Add-on"')
log('load', 'and pick any file inside dist-firefox (e.g. manifest.json).')
