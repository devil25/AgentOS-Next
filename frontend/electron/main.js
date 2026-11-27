const { app, BrowserWindow, WebContentsView, ipcMain, dialog, session, shell, nativeTheme, safeStorage, clipboard, Notification } = require('electron')
const path = require('path')
const os = require('os')
const { spawn, spawnSync } = require('child_process')
const http = require('http')
const fs = require('fs')
const { fileURLToPath } = require('url')
const runtimeConfig = require('../config/runtime.json')
const { backendLaunch } = require('./backend-launch.cjs')
const { createUpdateController } = require('./updates.cjs')
const { portInUse, startupFailureMessage } = require('./backend-startup.cjs')
const { showStartupMessage } = require('./message-window.cjs')
const { sendNativeKey, replaceFocusedText } = require('./embedded-browser-input.cjs')
const { backendPort, useDevelopmentRenderer } = require('./runtime-options.cjs')
const {
  generateControlSecret,
  generateControlSessionId,
  loadOrCreateControlProfileId,
  mintControlSession,
  normalizeBackendHost,
} = require('./control-auth')
const { SECRET_FIELDS, createCredentialVault } = require('./credential-vault')
const { createMcpCredentialVault, createMcpCredentialService } = require('./mcp-credential-vault.cjs')
const { isExternalOpenableUrl, windowOpenTarget } = require('./navigation-policy')
const CREDENTIAL_IDS = new Set(Object.keys(SECRET_FIELDS))
const MAX_IPC_STRING_LENGTH = 20_000

const BACKEND_HOST = normalizeBackendHost(
  runtimeConfig.backendHost,
  process.env.MONAW_ALLOW_UNSAFE_BACKEND_HOST === '1'
)
const BACKEND_PORT = backendPort(runtimeConfig)
const BACKEND_BASE_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`
const CONTROL_SECRET = generateControlSecret()
const CONTROL_SESSION_ID = generateControlSessionId()
let CONTROL_PROFILE_ID = null
const BACKEND_RESTART_EXIT_CODE = 78
const isDev = !app.isPackaged
const useDevServer = useDevelopmentRenderer(app.isPackaged)
const APP_USER_MODEL_ID = 'com.agentos.next'
const LEGACY_USER_DATA_DIR_NAMES = ['AI Agent', 'ai-agent']
const APP_THEME_COLORS = {
  dark: '#111b13',
  light: '#f7fbdf',
}
// The renderer draws its own app bar; on Windows/Linux the native caption
// buttons are overlaid on it, so their colors must match the bar surface.
const WINDOW_ACTIONS = new Set(['minimize', 'toggle-maximize', 'close'])

let mainWindow = null
let backendProcess = null
let backendUvCommand = null
let appQuitting = false
let backendReady = false
let updateController = null

function currentBackendLaunch() {
  return backendLaunch({
    packaged: !isDev,
    resourcesPath: process.resourcesPath,
    sourceBackendDir: path.join(__dirname, '../../backend'),
    uvCommand: backendUvCommand,
    host: BACKEND_HOST,
    port: BACKEND_PORT,
  })
}

function getMonawHomeDir() {
  return process.env.MONAW_HOME || process.env.AGENT_HOME || path.join(os.homedir(), '.monaw')
}

function installUserDataDir() {
  const userDataDir = path.join(getMonawHomeDir(), 'runtime', 'electron')
  fs.mkdirSync(userDataDir, { recursive: true })
  app.setPath('userData', userDataDir)
}

function getBackendRuntimeDir() {
  return path.join(getMonawHomeDir(), 'runtime')
}

function getBackendWorkspaceDir() {
  if (process.env.AGENT_WORKSPACE_DIR) return process.env.AGENT_WORKSPACE_DIR
  try {
    return path.join(app.getPath('documents'), 'monaw')
  } catch (err) {
    console.warn('[main] Documents folder unavailable; using the Monaw home workspace:', err.message)
    return path.join(getMonawHomeDir(), 'workspace')
  }
}

function rotateFileIfNeeded(filePath, maxBytes = 5 * 1024 * 1024) {
  try {
    if (!fs.existsSync(filePath) || fs.statSync(filePath).size <= maxBytes) return
    const rotatedPath = `${filePath}.1`
    if (fs.existsSync(rotatedPath)) fs.unlinkSync(rotatedPath)
    fs.renameSync(filePath, rotatedPath)
  } catch (err) {
    console.warn('[main] Failed to rotate log', filePath, err.message)
  }
}

function attachBackendLogging(runtimeDir, proc) {
  const logPath = path.join(runtimeDir, 'backend.log')
  rotateFileIfNeeded(logPath)
  const logStream = fs.createWriteStream(logPath, { flags: 'a' })
  const writeLog = (streamName, data) => {
    const text = data.toString()
    logStream.write(`[${new Date().toISOString()}] [${streamName}] ${text}`)
  }

  console.log('[main] Backend log file:', logPath)
  proc.stdout?.on('data', (d) => writeLog('stdout', d))
  proc.stderr?.on('data', (d) => writeLog('stderr', d))
  proc.on('error', (err) => {
    logStream.write(`[${new Date().toISOString()}] [error] ${err.stack || err.message}\n`)
    console.error('[backend] process error', err.message)
  })
  proc.on('exit', (code) => {
    const line = `[${new Date().toISOString()}] [exit] code=${code}\n`
    logStream.write(line)
    logStream.end()
    console.log('[backend] exited with code', code)
    if (backendProcess === proc) backendProcess = null
    if (!appQuitting && !backendReady && code !== BACKEND_RESTART_EXIT_CODE) {
      reportStartupFailure({ kind: 'exited', code }, logPath)
      return
    }
    if (!appQuitting && code === BACKEND_RESTART_EXIT_CODE) {
      console.log('[main] Backend requested restart; respawning')
      setTimeout(() => {
        spawnBackend()
        waitForBackend().then(applyStoredCredentialsToBackend).catch((err) => {
          console.error('[main] Backend restart did not become ready:', err.message)
        })
      }, 500)
    }
  })
}

function spawnBackend() {
  const runtimeDir = getBackendRuntimeDir()
  const workspaceDir = getBackendWorkspaceDir()
  fs.mkdirSync(runtimeDir, { recursive: true })
  fs.mkdirSync(workspaceDir, { recursive: true })

  const launch = currentBackendLaunch()

  console.log('[main] Spawning backend from', launch.cwd, 'with', launch.command)

  const proc = spawn(
    launch.command,
    launch.args,
    {
      cwd: launch.cwd,
      env: {
        ...process.env,
        ...launch.env,
        MONAW_CONTROL_SECRET: CONTROL_SECRET,
        MONAW_ALLOW_DEVELOPMENT_TOKEN: '0',
        CORS_ALLOW_ORIGINS: useDevServer
          ? 'http://localhost:5275,http://127.0.0.1:5275'
          : 'null',
        CORS_ALLOW_ORIGIN_REGEX: '',
        AGENT_RUNTIME_DIR: process.env.AGENT_RUNTIME_DIR || runtimeDir,
        AGENT_WORKSPACE_DIR: process.env.AGENT_WORKSPACE_DIR || workspaceDir,
        AGENTOS_EMBEDDED_BROWSER_PORT: String(embeddedBrowserBridgePort),
        AGENTOS_EMBEDDED_BROWSER_SECRET: EMBEDDED_BROWSER_BRIDGE_SECRET,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    }
  )
  backendProcess = proc
  attachBackendLogging(runtimeDir, proc)
}

installUserDataDir()
// One Monaw per data folder: a second launch would start a second backend on the same port.
const hasInstanceLock = app.requestSingleInstanceLock()
if (!hasInstanceLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  })
}

let startupFailureShown = false
let startupFailureWindow = null
function reportStartupFailure(reason, logPath = path.join(getBackendRuntimeDir(), 'backend.log')) {
  console.error('[main] Backend startup failed:', JSON.stringify(reason))
  // Keep the reason with the backend log, where support looks first.
  try {
    fs.mkdirSync(path.dirname(logPath), { recursive: true })
    fs.appendFileSync(logPath, `[${new Date().toISOString()}] [startup] failed ${JSON.stringify(reason)}\n`)
  } catch (err) {
    console.warn('[main] Failed to record startup failure', err.message)
  }
  if (startupFailureShown) return
  startupFailureShown = true
  const { title, message, detail } = startupFailureMessage(reason, { port: BACKEND_PORT, logPath })
  startupFailureWindow = showStartupMessage({
    BrowserWindow, parent: mainWindow, title, message, detail,
    theme: nativeTheme.shouldUseDarkColors ? 'dark' : 'light',
    onClose: () => {
      startupFailureWindow = null
      app.quit()
    },
  })
}
app.setName('AgentOS-Next')
if (process.platform === 'win32') {
  app.setAppUserModelId(APP_USER_MODEL_ID)
}

function runCommandAndCapture(cmd, args, options = {}) {
  return new Promise((resolve) => {
    const proc = spawn(cmd, args, options)
    let stdout = ''
    let stderr = ''
    proc.stdout?.on('data', (d) => { stdout += d.toString() })
    proc.stderr?.on('data', (d) => { stderr += d.toString() })
    proc.on('error', (err) => {
      resolve({ ok: false, code: -1, stdout, stderr: String(err) })
    })
    proc.on('close', (code) => {
      resolve({ ok: code === 0, code, stdout, stderr })
    })
  })
}

function resolveUvCandidates() {
  const candidates = []
  if (process.env.AGENT_UV_PATH) candidates.push(process.env.AGENT_UV_PATH)
  candidates.push(process.platform === 'win32' ? 'uv.exe' : 'uv')
  return [...new Set(candidates)]
}

async function pickBackendUv() {
  for (const candidate of resolveUvCandidates()) {
    const result = await runCommandAndCapture(candidate, ['--version'])
    if (result.ok) return candidate
  }
  return process.platform === 'win32' ? 'uv.exe' : 'uv'
}

function killBackend() {
  if (!backendProcess) return
  const proc = backendProcess
  backendProcess = null
  if (process.platform === 'win32') {
    const result = spawnSync(
      'taskkill',
      ['/pid', String(proc.pid), '/t', '/f'],
      {
        windowsHide: true,
        stdio: 'ignore',
      },
    )
    if (result.error || result.status !== 0) {
      try {
        proc.kill()
      } catch {
        // The backend may have exited between the check and taskkill.
      }
    }
  } else {
    proc.kill('SIGTERM')
  }
}

function waitForBackend(retries = 30, delay = 1000) {
  return new Promise((resolve, reject) => {
    let attempts = 0
    const check = () => {
      http
        .get(`${BACKEND_BASE_URL}/health`, (res) => {
          if (res.statusCode === 200) {
            backendReady = true
            console.log('[main] Backend ready')
            resolve()
          } else {
            retry()
          }
        })
        .on('error', retry)
    }
    const retry = () => {
      attempts++
      if (attempts >= retries) {
        reject(new Error('Backend failed to start'))
      } else {
        setTimeout(check, delay)
      }
    }
    check()
  })
}

function buildContentSecurityPolicy() {
  const backendHttp = BACKEND_BASE_URL
  const viteHttp = 'http://localhost:5275'
  const viteLoopbackHttp = 'http://127.0.0.1:5275'
  const viteWs = 'ws://localhost:5275'
  const viteLoopbackWs = 'ws://127.0.0.1:5275'

  if (useDevServer) {
    return [
      "default-src 'self'",
      `script-src 'self' 'unsafe-inline' 'unsafe-eval' ${viteHttp} ${viteLoopbackHttp}`,
      `connect-src 'self' ${backendHttp} ${viteHttp} ${viteLoopbackHttp} ${viteWs} ${viteLoopbackWs}`,
      `img-src 'self' data: blob: ${backendHttp}`,
      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
      "font-src 'self' https://fonts.gstatic.com",
      "object-src 'none'",
      "base-uri 'none'",
      "frame-ancestors 'none'",
      "form-action 'none'",
    ].join('; ')
  }

  return [
    "default-src 'self'",
    "script-src 'self'",
    `connect-src 'self' ${backendHttp}`,
    `img-src 'self' data: blob: ${backendHttp}`,
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "form-action 'none'",
  ].join('; ')
}

function installContentSecurityPolicy() {
  const policy = buildContentSecurityPolicy()
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [policy],
      },
    })
  })
}

function isTrustedRendererUrl(value) {
  if (!value) return false
  try {
    const url = new URL(value)
    if (useDevServer) return url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname) && url.port === '5275'
    if (url.protocol !== 'file:') return false
    return path.resolve(fileURLToPath(url)) === path.resolve(__dirname, '../dist/index.html')
  } catch {
    return false
  }
}

function isTrustedRenderer(webContents, value) {
  if (!mainWindow || webContents.id !== mainWindow.webContents.id) return false
  return isTrustedRendererUrl(value || webContents.getURL())
}

function updateBackendSettings(payload) {
  return new Promise((resolve, reject) => {
    const body = Buffer.from(JSON.stringify(payload), 'utf8')
    const session = mintControlSession(CONTROL_SECRET, CONTROL_SESSION_ID, undefined, undefined, CONTROL_PROFILE_ID)
    const request = http.request(
      `${BACKEND_BASE_URL}/api/settings`,
      {
        method: 'PUT',
        headers: {
          Authorization: `Bearer ${session.token}`,
          'Content-Type': 'application/json',
          'Content-Length': String(body.length),
        },
      },
      (response) => {
        const chunks = []
        response.on('data', (chunk) => chunks.push(chunk))
        response.on('end', () => {
          if (response.statusCode && response.statusCode >= 200 && response.statusCode < 300) {
            resolve()
            return
          }
          const detail = Buffer.concat(chunks).toString('utf8').slice(0, 500)
          reject(new Error(`Backend credential sync failed (${response.statusCode}): ${detail}`))
        })
      },
    )
    request.setTimeout(10_000, () => request.destroy(new Error('Backend credential sync timed out')))
    request.on('error', reject)
    request.on('finish', () => body.fill(0))
    request.end(body)
  })
}

function assertTrustedIpcSender(event) {
  if (!event?.sender || !isTrustedRenderer(event.sender, event.senderFrame?.url)) {
    throw new Error('IPC request rejected from untrusted renderer')
  }
}

function assertIpcString(value, name, maxLength = MAX_IPC_STRING_LENGTH) {
  if (typeof value !== 'string' || value.length > maxLength) {
    throw new Error(`IPC request rejected: invalid ${name}`)
  }
  return value
}

function assertOptionalIpcString(value, name, maxLength = MAX_IPC_STRING_LENGTH) {
  if (value === undefined || value === null || value === '') return ''
  return assertIpcString(value, name, maxLength)
}

function assertCredentialId(id) {
  const value = assertIpcString(id, 'credential id', 80)
  if (!CREDENTIAL_IDS.has(value)) {
    throw new Error('IPC request rejected: unsupported credential id')
  }
  return value
}

function assertTheme(value) {
  if (value !== 'light' && value !== 'dark') {
    throw new Error('IPC request rejected: invalid theme')
  }
  return value
}

function openExternalUrl(value) {
  if (!isExternalOpenableUrl(value)) return false
  shell.openExternal(value).catch((err) => {
    console.warn('[main] Failed to open external URL', value, err.message)
  })
  return true
}

function handleUntrustedNavigation(event, url) {
  if (isTrustedRendererUrl(url)) return
  if (openExternalUrl(url)) {
    event.preventDefault()
    return
  }
  event.preventDefault()
}

function installMediaPermissionHandler() {
  session.defaultSession.setPermissionCheckHandler((webContents, permission, requestingOrigin) => {
    const allowed = permission === 'media' && isTrustedRenderer(webContents, requestingOrigin)
    console.log('[main] Permission check', { permission, requestingOrigin, allowed })
    return allowed
  })
  session.defaultSession.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const requestingUrl = (details && details.requestingUrl) || webContents.getURL()
    const allowed = permission === 'media' && isTrustedRenderer(webContents, requestingUrl)
    console.log('[main] Permission request', { permission, requestingUrl, allowed })
    callback(allowed)
  })
}

function getAppIconPath() {
  const iconPath = path.join(__dirname, '../assets/app-icon.png')
  return fs.existsSync(iconPath) ? iconPath : undefined
}

function applyNativeTheme(theme) {
  const safeTheme = theme === 'light' ? 'light' : 'dark'
  nativeTheme.themeSource = safeTheme
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.setBackgroundColor(APP_THEME_COLORS[safeTheme])
  }
}


const EMBEDDED_PROVIDERS = Object.freeze({
  chatgpt: 'https://chatgpt.com/',
  gemini: 'https://gemini.google.com/app',
  deepseek: 'https://chat.deepseek.com/',
  zai: 'https://chat.z.ai/',
})
let embeddedBrowserView = null
let embeddedBrowserProvider = null
let embeddedBrowserVisible = false

function embeddedSafeUrl(value) {
  if (typeof value !== 'string' || value.length > 4096) throw new Error('Invalid browser URL')
  let parsed
  try { parsed = new URL(value) } catch { throw new Error('Enter a valid HTTPS URL') }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.port && parsed.port !== '443') {
    throw new Error('The embedded browser only allows secure HTTPS pages')
  }
  return parsed.href
}

function publishEmbeddedBrowserState(extra = {}) {
  if (!mainWindow || mainWindow.isDestroyed()) return
  const view = embeddedBrowserView
  const state = view && !view.webContents.isDestroyed()
    ? { open: embeddedBrowserVisible, provider: embeddedBrowserProvider, url: view.webContents.getURL(), title: view.webContents.getTitle(), loading: view.webContents.isLoading(), canGoBack: view.webContents.navigationHistory.canGoBack(), canGoForward: view.webContents.navigationHistory.canGoForward(), ...extra }
    : { open: false, provider: null, url: '', title: '', loading: false, canGoBack: false, canGoForward: false, ...extra }
  mainWindow.webContents.send('embedded-browser:state', state)
}

function hideEmbeddedBrowser() {
  if (!embeddedBrowserView || embeddedBrowserView.webContents.isDestroyed()) return
  embeddedBrowserVisible = false
  // Hide the native view without collapsing its viewport. A zero-sized
  // viewport makes provider DOM elements appear non-visible to automation.
  // Keep background throttling disabled so chat pages remain responsive.
  embeddedBrowserView.setVisible(false)
  publishEmbeddedBrowserState()
}

function closeEmbeddedBrowser() {
  if (!embeddedBrowserView) return
  const view = embeddedBrowserView
  embeddedBrowserView = null
  embeddedBrowserProvider = null
  embeddedBrowserVisible = false
  try { mainWindow?.contentView.removeChildView(view) } catch {}
  try { if (!view.webContents.isDestroyed()) view.webContents.close({ waitForBeforeUnload: false }) } catch {}
  publishEmbeddedBrowserState()
}


const EMBEDDED_PAGE_INSPECTION_SCRIPT = `(() => {
  const visible = (element) => {
    const rect = element.getBoundingClientRect()
    const style = getComputedStyle(element)
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
  }
  const text = String(document.body?.innerText || '').slice(0, 20000)
  const headings = Array.from(document.querySelectorAll('h1,h2')).filter(visible).slice(0, 30).map((element) => element.innerText.trim()).filter(Boolean)
  return { title: document.title, url: location.href, text, headings, inspectedAt: new Date().toISOString() }
})()`
let embeddedBrowserBridgeServer = null
let embeddedBrowserBridgePort = 0
const EMBEDDED_BROWSER_BRIDGE_SECRET = generateControlSecret()

const EMBEDDED_BROWSER_INTERACTION_SCRIPT = `(action, args) => {
  const visible = (element) => {
    const rect = element.getBoundingClientRect()
    const style = getComputedStyle(element)
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
  }
  const selector = 'button,a[href],input:not([type="password"]):not([type="hidden"]):not([type="file"]),textarea,[contenteditable="true"],[role="button"],[role="textbox"]'
  const elements = Array.from(document.querySelectorAll(selector)).filter(visible).slice(0, 200)
  const describe = (element, index) => ({
    index,
    tag: element.tagName.toLowerCase(),
    role: element.getAttribute('role') || '',
    type: element.getAttribute('type') || '',
    text: String(element.innerText || element.textContent || '').trim().slice(0, 240),
    label: String(element.getAttribute('aria-label') || element.getAttribute('title') || '').slice(0, 240),
    placeholder: String(element.getAttribute('placeholder') || '').slice(0, 240),
    disabled: Boolean(element.disabled),
    editable: element.isContentEditable || ['INPUT','TEXTAREA'].includes(element.tagName)
  })
  if (action === 'conversation') {
    const provider = location.hostname === 'chatgpt.com' || location.hostname === 'chat.openai.com' ? 'chatgpt'
      : location.hostname === 'gemini.google.com' ? 'gemini'
      : location.hostname === 'chat.deepseek.com' ? 'deepseek'
      : location.hostname === 'chat.z.ai' ? 'zai' : ''
    if (!provider) throw new Error('The embedded page is not a supported AI chat provider')
    const selectors = provider === 'chatgpt'
      ? '[data-message-author-role]'
      : provider === 'gemini'
        ? 'user-query, model-response, [data-message-id], [data-testid*="message"]'
        : provider === 'deepseek'
          ? '[data-message-id], [data-testid*="message"], [data-role], .ds-message, [class*="message"]'
          : '[data-message-id], [data-testid*="message"], [data-role], [data-author], [class*="message"]'
    let nodes = Array.from(document.querySelectorAll(selectors)).filter(visible)
    // ChatGPT may wrap each turn in an article/test-id container while the
    // author-role marker is nested deeper in the rendered turn.
    if (provider === 'chatgpt' && nodes.length === 0) {
      nodes = Array.from(document.querySelectorAll('article,[data-testid^="conversation-turn-"],[data-testid*="conversation-turn"]')).filter(visible)
    }
    nodes = nodes.slice(-100)
    const messages = nodes.map((node, index) => {
      const roleNode = node.matches('[data-message-author-role],[data-role],[data-author]')
        ? node
        : node.querySelector('[data-message-author-role],[data-role],[data-author],[data-sender]')
      const source = roleNode || node
      const roleValue = String(source.getAttribute('data-message-author-role') || source.getAttribute('data-role') || source.getAttribute('data-author') || source.getAttribute('data-sender') || source.getAttribute('data-testid') || source.getAttribute('aria-label') || source.getAttribute('role') || source.tagName || '').toLowerCase()
      const classes = String(source.className?.baseVal || source.className || '').toLowerCase()
      const role = /user|human|query/.test(roleValue + ' ' + classes) ? 'user'
        : /assistant|model|response/.test(roleValue + ' ' + classes) ? 'assistant' : ''
      const contentNode = source.querySelector('[data-message-content], .markdown, .message-content, .model-response-text') || source
      const text = String(contentNode.innerText || contentNode.textContent || '').trim().slice(0, 20000)
      const messageId = String(source.getAttribute('data-message-id') || node.getAttribute('data-message-id') || source.getAttribute('data-testid') || node.getAttribute('data-testid') || node.id || (role + '-' + index))
      return { message_id: messageId.slice(0, 300), role, text, complete: true }
    }).filter(item => item.role && item.text)
    const composer = Array.from(document.querySelectorAll('textarea,[contenteditable="true"],[role="textbox"]')).find(element => visible(element) && !element.disabled && !(element.tagName === 'INPUT' && element.type === 'password'))
    const loginRequired = Boolean(Array.from(document.querySelectorAll('input[type="password"]')).some(visible))
    // Do not treat a generic busy container as model generation: ChatGPT keeps
    // aria-busy on unrelated UI regions. Only explicit stop controls or a
    // streaming marker on an actual message count as generation.
    const stopControl = Array.from(document.querySelectorAll('button[data-testid*="stop"],button[aria-label*="Stop" i],[data-testid*="stop"]')).some(element => visible(element) && !element.disabled)
    const streamingMessage = nodes.some(node => node.getAttribute('data-is-streaming') === 'true' || Boolean(node.querySelector('[data-is-streaming="true"]')))
    const generating = stopControl || streamingMessage
    return { title: document.title, url: location.href, provider, messages, composerReady: Boolean(composer), generating, loginRequired, blockedReason: '' }
  }
  if (action === 'snapshot') return { title: document.title, url: location.href, elements: elements.map(describe) }
  const index = Number(args.index)
  if (!Number.isInteger(index) || index < 0 || index >= elements.length) throw new Error('Element index is no longer available; take a fresh snapshot')
  const element = elements[index]
  if (action === 'click') {
    if (element.disabled) throw new Error('The selected element is disabled')
    element.scrollIntoView({ block: 'center', inline: 'nearest' })
    element.click()
    return { ok: true, action, index, description: describe(element, index) }
  }
  if (action === 'type') {
    if (element.disabled || !(element.isContentEditable || ['INPUT','TEXTAREA'].includes(element.tagName))) throw new Error('The selected element is not an editable text field')
    if (element.tagName === 'INPUT' && ['password','file','hidden'].includes(String(element.type).toLowerCase())) throw new Error('This input type is not supported')
    const value = String(args.text || '')
    if (value.length > 4000) throw new Error('Text is too long (maximum 4000 characters)')
    element.focus()
    if (document.activeElement !== element && !element.contains(document.activeElement)) {
      throw new Error('The selected text field could not receive focus')
    }
    // Actual text insertion is performed by Electron's native WebContents input API.
    return { ok: true, action, index, characters: value.length }
  }
  throw new Error('Unsupported embedded browser action')
}`

async function inspectEmbeddedBrowserPage() {
  if (!embeddedBrowserView || embeddedBrowserView.webContents.isDestroyed()) throw new Error('The embedded browser is not open')
  const result = await embeddedBrowserView.webContents.executeJavaScript(EMBEDDED_PAGE_INSPECTION_SCRIPT, true)
  return { ...result, url: embeddedSafeUrl(embeddedBrowserView.webContents.getURL()) }
}


async function inspectEmbeddedAccessibilityMessages() {
  if (!embeddedBrowserView || embeddedBrowserView.webContents.isDestroyed()) return []
  const debuggerClient = embeddedBrowserView.webContents.debugger
  let attachedHere = false
  try {
    if (!debuggerClient.isAttached()) {
      debuggerClient.attach('1.3')
      attachedHere = true
    }
    const tree = await debuggerClient.sendCommand('Accessibility.getFullAXTree')
    const nodes = Array.isArray(tree?.nodes) ? tree.nodes : []
    const messages = []
    for (const node of nodes) {
      const role = String(node?.role?.value || '').toLowerCase()
      if (!['article', 'group', 'region'].includes(role) || !Number.isInteger(node.backendDOMNodeId)) continue
      let objectId
      try {
        const resolved = await debuggerClient.sendCommand('DOM.resolveNode', {
          backendNodeId: node.backendDOMNodeId,
        })
        objectId = resolved?.object?.objectId
        if (!objectId) continue
        const inspected = await debuggerClient.sendCommand('Runtime.callFunctionOn', {
          objectId,
          functionDeclaration: `function () {
            const attrs = ['data-message-author-role','data-role','data-author','data-sender','data-message-id','data-testid','aria-label']
            const attributes = Object.fromEntries(attrs.map(name => [name, this.getAttribute(name) || '']))
            const nested = this.querySelector('[data-message-author-role],[data-role],[data-author],[data-sender]')
            if (nested) {
              for (const name of attrs) attributes[name] = nested.getAttribute(name) || attributes[name]
            }
            return { attributes, text: String(this.innerText || this.textContent || '').trim().slice(0, 20000) }
          }`,
          returnByValue: true,
        })
        const value = inspected?.result?.value
        if (!value?.text) continue
        const attrs = value.attributes || {}
        const hint = [attrs['data-message-author-role'], attrs['data-role'], attrs['data-author'],
          attrs['data-sender'], attrs['aria-label'], attrs['data-testid'], node.name?.value].join(' ').toLowerCase()
        const messageRole = /user|human|you said/.test(hint) ? 'user'
          : /assistant|chatgpt|model response|chatgpt said/.test(hint) ? 'assistant' : ''
        if (!messageRole) continue
        const identity = attrs['data-message-id'] || attrs['data-testid'] || node.nodeId
        messages.push({
          message_id: String(identity).slice(0, 300),
          role: messageRole,
          text: value.text,
          complete: true,
        })
      } catch {
        // A stale AX backend node is skipped; the next snapshot resolves fresh IDs.
      } finally {
        if (objectId) {
          try {
            await debuggerClient.sendCommand('Runtime.releaseObject', { objectId })
          } catch {}
        }
      }
    }
    const seen = new Set()
    return messages.filter(message => {
      const key = message.role + ':' + message.message_id
      if (seen.has(key)) return false
      seen.add(key)
      return true
    }).slice(-100)
  } catch (error) {
    console.warn('[embedded-browser] Accessibility snapshot unavailable:', String(error?.message || error).slice(0, 300))
    return []
  } finally {
    if (attachedHere && debuggerClient.isAttached()) {
      try { debuggerClient.detach() } catch {}
    }
  }
}

function startEmbeddedBrowserBridge() {
  return new Promise((resolve, reject) => {
    embeddedBrowserBridgeServer = http.createServer(async (request, response) => {
      const remote = request.socket.remoteAddress || ''
      const loopback = remote === '127.0.0.1' || remote === '::1' || remote === '::ffff:127.0.0.1'
      const offered = String(request.headers.authorization || '')
      const expected = `Bearer ${EMBEDDED_BROWSER_BRIDGE_SECRET}`
      const offeredBuffer = Buffer.from(offered)
      const expectedBuffer = Buffer.from(expected)
      const authorized = offeredBuffer.length === expectedBuffer.length && require('crypto').timingSafeEqual(offeredBuffer, expectedBuffer)
      const send = (status, payload) => {
        response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' })
        response.end(JSON.stringify(payload))
      }
      if (!loopback || !authorized) return send(401, { error: 'Unauthorized' })
      if (request.method !== 'POST' || !['/v1/inspect', '/v1/action', '/v1/conversation'].includes(request.url)) return send(404, { error: 'Not found' })
      let body = ''
      for await (const chunk of request) {
        body += chunk.toString('utf8')
        if (body.length > 16 * 1024) return send(413, { error: 'Request too large' })
      }
      try {
        if (request.url === '/v1/inspect') return send(200, await inspectEmbeddedBrowserPage())
        if (request.url === '/v1/conversation') {
          if (!embeddedBrowserView || embeddedBrowserView.webContents.isDestroyed()) throw new Error('The embedded browser is not open')
          const script = `(${EMBEDDED_BROWSER_INTERACTION_SCRIPT})('conversation', {})`
          const snapshot = await embeddedBrowserView.webContents.executeJavaScript(script, true)
          if (snapshot?.provider === 'chatgpt' && (!Array.isArray(snapshot.messages) || snapshot.messages.length === 0)) {
            // ChatGPT has changed its rendered DOM more than once. Fall back to
            // Chromium's accessibility tree, the same source used by the
            // original AgentOS browser extension, instead of guessing CSS roles.
            snapshot.messages = await inspectEmbeddedAccessibilityMessages()
            snapshot.captureSource = snapshot.messages.length ? 'accessibility_tree' : 'none'
          } else {
            snapshot.captureSource = 'dom'
          }
          return send(200, snapshot)
        }
        const payload = body ? JSON.parse(body) : {}
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return send(400, { error: 'Invalid action payload' })
        if (!embeddedBrowserView || embeddedBrowserView.webContents.isDestroyed()) throw new Error('The embedded browser is not open')
        if (payload.action === 'navigate') {
          const url = embeddedSafeUrl(payload.url)
          await embeddedBrowserView.webContents.loadURL(url)
          return send(200, { ok: true, action: 'navigate', url: embeddedSafeUrl(embeddedBrowserView.webContents.getURL()) })
        }
        const allowed = new Set(['snapshot', 'click', 'type', 'press_key'])
        if (!allowed.has(payload.action)) return send(400, { error: 'Unsupported embedded browser action' })
        const webContents = embeddedBrowserView.webContents
        if (payload.action === 'press_key') {
          const key = String(payload.key || '')
          if (!['Enter','Escape','Tab','ArrowUp','ArrowDown','Backspace'].includes(key)) {
            return send(400, { error: 'Unsupported key' })
          }
          // ChatGPT can be configured to keep Enter as a newline. When the
          // composer contains text, prefer its explicit enabled Send button;
          // this confirms a submit action instead of assuming Enter sent it.
          if (key === 'Enter' && ['chatgpt.com', 'chat.openai.com'].includes(new URL(webContents.getURL()).hostname)) {
            const submitted = await webContents.executeJavaScript(`(() => {
              const visible = (element) => {
                const rect = element.getBoundingClientRect()
                const style = getComputedStyle(element)
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
              }
              const composer = Array.from(document.querySelectorAll('textarea,[contenteditable="true"],[role="textbox"]'))
                .find(element => visible(element) && !element.disabled)
              const text = composer ? String(composer.value ?? composer.innerText ?? composer.textContent ?? '').trim() : ''
              if (!text) return { submitted: false, reason: 'composer_empty' }
              const send = Array.from(document.querySelectorAll('button[data-testid="send-button"],button[aria-label*="Send" i],button[aria-label*="Send message" i]'))
                .find(element => visible(element) && !element.disabled && element.getAttribute('aria-disabled') !== 'true')
              if (!send) return { submitted: false, reason: 'send_button_unavailable' }
              send.click()
              return { submitted: true, method: 'send_button' }
            })()`, true)
            if (submitted?.submitted) {
              return send(200, { ok: true, action: 'press_key', key, ...submitted })
            }
            if (submitted?.reason === 'composer_empty') {
              return send(409, { error: 'The ChatGPT composer is empty; no message was submitted.' })
            }
          }
          sendNativeKey(webContents, key)
          return send(200, { ok: true, action: 'press_key', key, submitted: key === 'Enter' ? 'unverified_native_enter' : false })
        }
        const args = { index: payload.index, text: payload.text }
        const script = `(${EMBEDDED_BROWSER_INTERACTION_SCRIPT})(${JSON.stringify(payload.action)}, ${JSON.stringify(args)})`
        const result = await webContents.executeJavaScript(script, true)
        if (payload.action === 'type') {
          const value = String(payload.text || '')
          if (value.length > 4000) return send(413, { error: 'Text is too long (maximum 4000 characters)' })
          const inserted = replaceFocusedText(webContents, value, process.platform, payload.append === true)
          return send(200, { ...result, ...inserted })
        }
        return send(200, result)
      } catch (error) {
        const detail = String(error && error.message || 'Embedded browser action failed').slice(0, 500)
        console.warn('[embedded-browser] bridge request failed', { path: request.url, error: detail })
        send(409, { error: detail })
      }
    })
    embeddedBrowserBridgeServer.once('error', reject)
    embeddedBrowserBridgeServer.listen(0, '127.0.0.1', () => {
      const address = embeddedBrowserBridgeServer.address()
      embeddedBrowserBridgePort = address && typeof address === 'object' ? address.port : 0
      if (!embeddedBrowserBridgePort) return reject(new Error('Embedded browser bridge did not bind a port'))
      console.log('[embedded-browser] Authenticated loopback bridge ready')
      resolve()
    })
  })
}

function stopEmbeddedBrowserBridge() {
  if (!embeddedBrowserBridgeServer) return
  embeddedBrowserBridgeServer.close()
  embeddedBrowserBridgeServer = null
  embeddedBrowserBridgePort = 0
}

function attachEmbeddedBrowser(provider, { visible = true } = {}) {
  if (!Object.hasOwn(EMBEDDED_PROVIDERS, provider)) throw new Error('Unsupported AI provider')
  if (embeddedBrowserView && embeddedBrowserProvider === provider && !embeddedBrowserView.webContents.isDestroyed()) {
    embeddedBrowserVisible = visible
    if (visible) {
      const [width, height] = mainWindow.getContentSize()
      embeddedBrowserView.setBounds({ x: 0, y: 96, width, height: Math.max(1, height - 96) })
    }
    embeddedBrowserView.setVisible(visible)
    publishEmbeddedBrowserState()
    return true
  }
  closeEmbeddedBrowser()
  const browserSession = session.fromPartition(`persist:agentos-embedded-${provider}`)
  // Remote sites receive no Electron permissions by default. A future explicit
  // permission flow can grant individual capabilities after user confirmation.
  browserSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  const view = new WebContentsView({
    webPreferences: {
      session: browserSession,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: true,
    },
  })
  embeddedBrowserView = view
  embeddedBrowserProvider = provider
  embeddedBrowserVisible = visible
  // This page is also the reasoning transport surface. Keep it alive and
  // responsive while hidden or while the AgentOS window is minimized.
  view.webContents.setBackgroundThrottling(false)
  view.webContents.setWindowOpenHandler(({ url }) => {
    try { view.webContents.loadURL(embeddedSafeUrl(url)) } catch {}
    return { action: 'deny' }
  })
  const publish = () => publishEmbeddedBrowserState()
  for (const event of ['did-start-loading', 'did-stop-loading', 'did-navigate', 'did-navigate-in-page', 'page-title-updated', 'did-fail-load']) {
    view.webContents.on(event, publish)
  }
  view.webContents.on('will-navigate', (event, url) => {
    try { embeddedSafeUrl(url) } catch { event.preventDefault() }
  })
  mainWindow.contentView.addChildView(view)
  const [width, height] = mainWindow.getContentSize()
  view.setBounds({ x: 0, y: 96, width, height: Math.max(1, height - 96) })
  view.setVisible(visible)
  view.webContents.loadURL(EMBEDDED_PROVIDERS[provider]).catch((error) => {
    console.error('[embedded-browser] Navigation failed:', error.message)
    publishEmbeddedBrowserState({ error: 'The selected website could not be loaded.' })
  })
  publish()
  return true
}

function createWindow() {
  const appIconPath = getAppIconPath()
  if (process.platform === 'darwin' && appIconPath && app.dock) {
    app.dock.setIcon(appIconPath)
  }

  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    // Show only after Chromium has painted the first frame to avoid a blank flash.
    // Explicitly showing also makes the app visible when the launcher is started hidden.
    show: false,
    // On Windows and Linux the renderer draws its own window buttons in the
    // app bar (window:control), so no native caption buttons are shown.
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'hidden',
    ...(process.platform === 'darwin'
      ? { frame: false, trafficLightPosition: { x: 16, y: 15 } }
      : {}),
    autoHideMenuBar: true,
    backgroundColor: APP_THEME_COLORS.dark,
    ...(appIconPath ? { icon: appIconPath } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: isDev && process.env.MONAW_DEVTOOLS === '1',
      additionalArguments: [`--monaw-backend-base-url=${BACKEND_BASE_URL}`],
    },
  })

  mainWindow.once('ready-to-show', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return
    mainWindow.show()
    mainWindow.focus()
    console.log('[main] AgentOS-Next window shown')
  })
  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error('[main] Renderer failed to load', { errorCode, errorDescription, validatedURL })
  })

  if (useDevServer) {
    mainWindow.loadURL('http://localhost:5275').catch((err) => {
      console.error('[main] Failed to load development renderer:', err.message)
    })
    if (process.env.MONAW_DEVTOOLS === '1') {
      mainWindow.webContents.openDevTools({ mode: 'detach' })
    }
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html')).catch((err) => {
      console.error('[main] Failed to load packaged renderer:', err.message)
    })
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (windowOpenTarget(url, { isTrustedRendererUrl, backendBaseUrl: BACKEND_BASE_URL }) === 'external') {
      openExternalUrl(url)
    }
    return { action: 'deny' }
  })

  mainWindow.webContents.on('will-navigate', handleUntrustedNavigation)
  mainWindow.webContents.on('will-frame-navigate', handleUntrustedNavigation)
  mainWindow.webContents.on('will-redirect', handleUntrustedNavigation)

  const sendMaximized = () => mainWindow?.webContents.send('window:maximized', mainWindow.isMaximized())
  mainWindow.on('maximize', sendMaximized)
  mainWindow.on('unmaximize', sendMaximized)
  mainWindow.on('closed', () => { closeEmbeddedBrowser(); mainWindow = null })
}

app.whenReady().then(async () => {
  if (!hasInstanceLock) return
  try {
    CONTROL_PROFILE_ID = loadOrCreateControlProfileId({
      safeStorage,
      store: await getStore(),
      profileContext: getMonawHomeDir(),
      platform: process.platform,
    })
  } catch (err) {
    // A missing OS encryption service disables cross-restart Run/Undo identity.
    // Never fall back to a renderer-provided or plaintext profile id.
    CONTROL_PROFILE_ID = null
    console.error('[main] Profile-bound recovery is unavailable:', err.message)
  }
  installContentSecurityPolicy()
  installMediaPermissionHandler()
  if (await portInUse(BACKEND_HOST, BACKEND_PORT)) {
    reportStartupFailure({ kind: 'port_in_use' })
    return
  }
  if (!currentBackendLaunch().frozen) backendUvCommand = await pickBackendUv()
  try {
    await startEmbeddedBrowserBridge()
  } catch (error) {
    console.error('[embedded-browser] Local read bridge unavailable:', error.message)
    stopEmbeddedBrowserBridge()
  }
  spawnBackend()
  createWindow()
  // Start the default signed-in ChatGPT session with AgentOS. Keep its
  // WebContentsView hidden from the UI but fully sized and active for tools.
  try {
    attachEmbeddedBrowser('chatgpt', { visible: false })
    console.log('[embedded-browser] Background ChatGPT session initialized')
  } catch (error) {
    console.error('[embedded-browser] Background session initialization failed:', error.message)
  }

  const updatesEnabled = app.isPackaged && process.platform === 'win32'
  const updater = updatesEnabled ? require('electron-updater').autoUpdater : null
  updateController = createUpdateController({
    updater, version: app.getVersion(), enabled: updatesEnabled,
    installDirectory: updatesEnabled ? path.dirname(process.execPath) : undefined,
    publish: (state) => {
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('updates:state', state)
      if (state.status === 'ready' && Notification.isSupported()) {
        const notice = new Notification({ title: 'AgentOS-Next update ready', body: `Version ${state.latestVersion} is ready. Open Settings > About to install it.` })
        notice.on('click', () => { if (mainWindow && !mainWindow.isDestroyed()) { mainWindow.show(); mainWindow.focus() } })
        notice.show()
      }
    },
  })
  if (updatesEnabled) {
    const firstCheck = setTimeout(() => { void updateController.check() }, 30_000)
    const periodicCheck = setInterval(() => { void updateController.check() }, 6 * 60 * 60 * 1000)
    firstCheck.unref()
    periodicCheck.unref()
  }

  waitForBackend().then(applyStoredCredentialsToBackend).catch((err) => {
    console.error('[main] Backend startup or credential sync failed:', err.message)
  })

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  appQuitting = true
  killBackend()
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  appQuitting = true
  killBackend()
})

let store = null
function readLegacyStoreData() {
  const appDataDir = app.getPath('appData')
  const currentUserData = path.resolve(app.getPath('userData'))
  const merged = {}
  for (const dirName of LEGACY_USER_DATA_DIR_NAMES) {
    const configPath = path.join(appDataDir, dirName, 'config.json')
    const legacyUserData = path.resolve(path.dirname(configPath))
    if (legacyUserData === currentUserData || !fs.existsSync(configPath)) continue
    try {
      const parsed = JSON.parse(fs.readFileSync(configPath, 'utf8'))
      if (parsed && typeof parsed === 'object') Object.assign(merged, parsed)
    } catch (err) {
      console.warn('[main] Failed to read legacy key store', configPath, err.message)
    }
  }
  return Object.keys(merged).length > 0 ? merged : null
}

function legacyConfigPaths() {
  const appDataDir = app.getPath('appData')
  const currentUserData = path.resolve(app.getPath('userData'))
  return LEGACY_USER_DATA_DIR_NAMES
    .map((dirName) => path.join(appDataDir, dirName, 'config.json'))
    .filter((configPath) => path.resolve(path.dirname(configPath)) !== currentUserData)
}

function deleteLegacyStoreKey(key) {
  for (const configPath of legacyConfigPaths()) {
    if (!fs.existsSync(configPath)) continue
    try {
      const parsed = JSON.parse(fs.readFileSync(configPath, 'utf8'))
      if (!parsed || typeof parsed !== 'object' || !(key in parsed)) continue
      delete parsed[key]
      fs.writeFileSync(configPath, JSON.stringify(parsed, null, 2), 'utf8')
    } catch (err) {
      console.warn('[main] Failed to delete legacy key', configPath, key, err.message)
    }
  }
}

async function getStore() {
  if (!store) {
    const { default: Store } = await import('electron-store')
    store = new Store()
  }
  return store
}

let credentialVault = null
let mcpCredentialService = null
async function getMcpCredentialService() {
  if (!mcpCredentialService) {
    mcpCredentialService = getStore().then(currentStore => createMcpCredentialService({
      vault: createMcpCredentialVault({ safeStorage, store: currentStore }),
      request: requestMcpBackend,
    }))
  }
  return mcpCredentialService
}

function requestMcpBackend(method, suffix, payload) {
  return new Promise((resolve, reject) => {
    const body = payload === undefined ? null : Buffer.from(JSON.stringify(payload), 'utf8')
    // This scope never appears in control:get-session or in renderer state.
    const session = mintControlSession(CONTROL_SECRET, CONTROL_SESSION_ID, undefined, ['mcp:credentials'])
    const request = http.request(`${BACKEND_BASE_URL}/api/settings/mcp-credentials${suffix}`, {
      method, headers: { Authorization: `Bearer ${session.token}`, 'Content-Type': 'application/json',
        ...(body ? { 'Content-Length': String(body.length) } : {}) },
    }, (response) => {
      const chunks = []
      let size = 0
      response.on('data', chunk => {
        size += chunk.length
        if (size > 512 * 1024) request.destroy(new Error('MCP credential response exceeds its limit'))
        else chunks.push(chunk)
      })
      response.on('end', () => {
        try {
          if (response.statusCode < 200 || response.statusCode >= 300) {
            throw new Error(`MCP credential operation failed (${response.statusCode}); reload saved settings and retry`)
          }
          resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')))
        } catch { reject(new Error('MCP credential response was unsuccessful or invalid; reload saved settings and retry')) }
        finally { for (const chunk of chunks) chunk.fill(0) }
      })
    })
    request.setTimeout(20_000, () => request.destroy(new Error('MCP credential handoff timed out; retry to reconcile')))
    request.on('error', reject)
    request.on('close', () => body?.fill(0))
    request.end(body)
  })
}

async function getCredentialVault() {
  if (!credentialVault) {
    const currentStore = await getStore()
    const candidate = createCredentialVault({
      safeStorage,
      store: currentStore,
      legacyData: readLegacyStoreData() || {},
      deleteLegacyValue: deleteLegacyStoreKey,
    })
    candidate.migrate()
    credentialVault = candidate
  }
  return credentialVault
}

async function applyStoredCredentialsToBackend() {
  const vault = await getCredentialVault()
  const payload = vault.backendPayload()
  try {
    if (Object.keys(payload).length > 0) {
      await updateBackendSettings(payload)
    }
    await (await getMcpCredentialService()).apply()
    return vault.status()
  } finally {
    for (const key of Object.keys(payload)) {
      payload[key] = ''
    }
  }
}

ipcMain.handle('control:get-session', async (event) => {
  assertTrustedIpcSender(event)
  return mintControlSession(CONTROL_SECRET, CONTROL_SESSION_ID, undefined, undefined, CONTROL_PROFILE_ID)
})

ipcMain.handle('updates:status', (event) => {
  assertTrustedIpcSender(event)
  return updateController?.getState() || { currentVersion: app.getVersion(), status: 'unavailable' }
})

ipcMain.handle('updates:check', async (event) => {
  assertTrustedIpcSender(event)
  return updateController ? updateController.check() : { currentVersion: app.getVersion(), status: 'unavailable' }
})

ipcMain.handle('updates:install', (event) => {
  assertTrustedIpcSender(event)
  return updateController?.install() || false
})

ipcMain.handle('credentials:status', async (event) => {
  assertTrustedIpcSender(event)
  return (await getCredentialVault()).status()
})

ipcMain.handle('credentials:set', async (event, id, value) => {
  assertTrustedIpcSender(event)
  const credentialId = assertCredentialId(id)
  const credentialValue = assertIpcString(value, 'credential value')
  const vault = await getCredentialVault()
  vault.setCredential(credentialId, credentialValue)
  await updateBackendSettings({ [SECRET_FIELDS[credentialId].backendField]: credentialValue })
  return vault.status()
})

ipcMain.handle('credentials:delete', async (event, id) => {
  assertTrustedIpcSender(event)
  const credentialId = assertCredentialId(id)
  const vault = await getCredentialVault()
  vault.deleteCredential(credentialId)
  await updateBackendSettings({ [SECRET_FIELDS[credentialId].backendField]: '' })
  return vault.status()
})

ipcMain.handle('credentials:apply', async (event) => {
  assertTrustedIpcSender(event)
  return applyStoredCredentialsToBackend()
})

ipcMain.handle('mcp:credential', async (event, input) => {
  assertTrustedIpcSender(event)
  try {
    return await (await getMcpCredentialService()).operate(input)
  } catch {
    // Neither OS/SDK exceptions nor transport responses may echo plaintext.
    throw new Error('MCP credential change could not complete. Check saved server configuration and OS encryption, then retry; a prepared migration will resume safely.')
  }
})

ipcMain.handle('theme:set', async (event, theme) => {
  assertTrustedIpcSender(event)
  applyNativeTheme(assertTheme(theme))
})

ipcMain.handle('window:control', async (event, action) => {
  assertTrustedIpcSender(event)
  if (!WINDOW_ACTIONS.has(action)) throw new Error('IPC request rejected: invalid window action')
  const window = BrowserWindow.fromWebContents(event.sender)
  if (!window) return false
  if (action === 'minimize') window.minimize()
  else if (action === 'close') window.close()
  else if (window.isMaximized()) window.unmaximize()
  else window.maximize()
  return window.isMaximized()
})


ipcMain.handle('embedded-browser:open', async (event, provider) => {
  assertTrustedIpcSender(event)
  if (typeof provider !== 'string') throw new Error('Invalid browser provider')
  return attachEmbeddedBrowser(provider)
})

ipcMain.handle('embedded-browser:navigate', async (event, value) => {
  assertTrustedIpcSender(event)
  if (!embeddedBrowserView || embeddedBrowserView.webContents.isDestroyed()) throw new Error('The embedded browser is not open')
  await embeddedBrowserView.webContents.loadURL(embeddedSafeUrl(value))
  return true
})

ipcMain.handle('embedded-browser:back', async (event) => {
  assertTrustedIpcSender(event)
  if (!embeddedBrowserView || !embeddedBrowserView.webContents.navigationHistory.canGoBack()) return false
  embeddedBrowserView.webContents.navigationHistory.goBack()
  return true
})

ipcMain.handle('embedded-browser:forward', async (event) => {
  assertTrustedIpcSender(event)
  if (!embeddedBrowserView || !embeddedBrowserView.webContents.navigationHistory.canGoForward()) return false
  embeddedBrowserView.webContents.navigationHistory.goForward()
  return true
})

ipcMain.handle('embedded-browser:reload', async (event) => {
  assertTrustedIpcSender(event)
  if (!embeddedBrowserView) return false
  embeddedBrowserView.webContents.reload()
  return true
})

ipcMain.handle('embedded-browser:bounds', async (event, bounds) => {
  assertTrustedIpcSender(event)
  if (!embeddedBrowserView || !bounds || typeof bounds !== 'object') return false
  const values = ['x', 'y', 'width', 'height'].map((key) => Number(bounds[key]))
  if (!values.every(Number.isFinite) || values[2] < 0 || values[3] < 0) throw new Error('Invalid browser viewport bounds')
  const [windowWidth, windowHeight] = mainWindow.getContentSize()
  const x = Math.max(0, Math.min(windowWidth, values[0]))
  const y = Math.max(0, Math.min(windowHeight, values[1]))
  const width = Math.max(0, Math.min(windowWidth - x, values[2]))
  const height = Math.max(0, Math.min(windowHeight - y, values[3]))
  embeddedBrowserView.setBounds({ x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) })
  return true
})

ipcMain.handle('embedded-browser:close', async (event) => {
  assertTrustedIpcSender(event)
  hideEmbeddedBrowser()
  return true
})

ipcMain.handle('embedded-browser:inspect', async (event) => {
  assertTrustedIpcSender(event)
  if (!embeddedBrowserView || embeddedBrowserView.webContents.isDestroyed()) throw new Error('The embedded browser is not open')
  return inspectEmbeddedBrowserPage()
})

ipcMain.handle('storage:clear-app-cache', async (event) => {
  assertTrustedIpcSender(event)
  // Cached pages, compiled scripts and shaders; settings and sign-ins stay.
  const appSession = event.sender.session
  await appSession.clearCache()
  await appSession.clearCodeCaches({})
  await appSession.clearStorageData({ storages: ['shadercache'] })
  return true
})

ipcMain.handle('window:is-maximized', async (event) => {
  assertTrustedIpcSender(event)
  return Boolean(BrowserWindow.fromWebContents(event.sender)?.isMaximized())
})

ipcMain.handle('clipboard:write-text', async (event, text) => {
  assertTrustedIpcSender(event)
  clipboard.writeText(assertIpcString(text, 'clipboard text'))
})

ipcMain.handle('dialog:select-directory', async (event, defaultPath) => {
  assertTrustedIpcSender(event)
  const safeDefaultPath = assertOptionalIpcString(defaultPath, 'default path', 4096)
  const result = await dialog.showOpenDialog(mainWindow, {
    title: 'Choose folder',
    defaultPath: safeDefaultPath || undefined,
    properties: ['openDirectory', 'createDirectory'],
  })
  if (result.canceled || !result.filePaths.length) return ''
  return result.filePaths[0]
})
