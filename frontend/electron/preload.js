const { contextBridge, ipcRenderer } = require('electron')

const backendBaseUrlPrefix = '--monaw-backend-base-url='
const backendBaseUrlArgument = process.argv.find((value) => value.startsWith(backendBaseUrlPrefix))
const backendBaseUrlCandidate = backendBaseUrlArgument
  ? backendBaseUrlArgument.slice(backendBaseUrlPrefix.length)
  : ''
const backendBaseUrl = /^http:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):\d+$/.test(backendBaseUrlCandidate)
  ? backendBaseUrlCandidate
  : ''
const credentialIds = new Set(['openai', 'google', 'compatible', 'tavily', 'telegramBot'])
const windowActions = new Set(['minimize', 'toggle-maximize', 'close'])

function windowAction(value) {
  if (typeof value !== 'string' || !windowActions.has(value)) {
    throw new TypeError('Unsupported window action')
  }
  return value
}

function onWindowMaximized(listener) {
  if (typeof listener !== 'function') throw new TypeError('Invalid window listener')
  const handler = (_event, maximized) => listener(Boolean(maximized))
  ipcRenderer.on('window:maximized', handler)
  return () => ipcRenderer.removeListener('window:maximized', handler)
}

function credentialId(value) {
  if (typeof value !== 'string' || !credentialIds.has(value)) {
    throw new TypeError('Unsupported credential id')
  }
  return value
}

function safeString(value, name, maxLength = 20000) {
  if (typeof value !== 'string' || value.length > maxLength) {
    throw new TypeError(`Invalid ${name}`)
  }
  return value
}

function optionalString(value, name, maxLength = 4096) {
  if (value === undefined || value === null || value === '') return ''
  return safeString(value, name, maxLength)
}

function theme(value) {
  if (value !== 'dark' && value !== 'light') {
    throw new TypeError('Invalid theme')
  }
  return value
}

function mcpCredential(input) {
  if (!input || typeof input !== 'object' || !['migrate', 'replace', 'delete'].includes(input.action)) {
    throw new TypeError('Invalid MCP credential operation')
  }
  const b = input.binding
  if (!b || !/^[a-f0-9]{32}$/.test(b.server_id) || !['stdio', 'streamable_http'].includes(b.transport)
    || b.target !== (b.transport === 'stdio' ? 'env' : 'headers')
    || typeof b.key !== 'string' || !/^[A-Za-z_][A-Za-z0-9_.-]{0,127}$/.test(b.key)) {
    throw new TypeError('Invalid MCP credential binding')
  }
  return { action: input.action, binding: { server_id: b.server_id, transport: b.transport, target: b.target, key: b.key },
    ...(input.action === 'replace' ? { value: safeString(input.value, 'MCP credential value') } : {}) }
}


const embeddedProviders = new Set(['chatgpt', 'gemini', 'deepseek', 'zai'])
const embeddedProvider = (value) => {
  if (typeof value !== 'string' || !embeddedProviders.has(value)) throw new TypeError('Unsupported embedded browser provider')
  return value
}
const embeddedUrl = (value) => {
  const safe = safeString(value, 'browser URL', 4096)
  if (!/^https:\/\//i.test(safe)) throw new TypeError('The embedded browser only allows HTTPS URLs')
  return safe
}
function onEmbeddedBrowserState(listener) {
  if (typeof listener !== 'function') throw new TypeError('Invalid embedded browser listener')
  const handler = (_event, state) => listener(state)
  ipcRenderer.on('embedded-browser:state', handler)
  return () => ipcRenderer.removeListener('embedded-browser:state', handler)
}
const embeddedBrowser = Object.freeze({
  open: (provider) => ipcRenderer.invoke('embedded-browser:open', embeddedProvider(provider)),
  navigate: (url) => ipcRenderer.invoke('embedded-browser:navigate', embeddedUrl(url)),
  back: () => ipcRenderer.invoke('embedded-browser:back'),
  forward: () => ipcRenderer.invoke('embedded-browser:forward'),
  reload: () => ipcRenderer.invoke('embedded-browser:reload'),
  setBounds: (bounds) => {
    if (!bounds || typeof bounds !== 'object') throw new TypeError('Invalid browser viewport bounds')
    for (const key of ['x', 'y', 'width', 'height']) {
      if (!Number.isFinite(bounds[key])) throw new TypeError('Invalid browser viewport bounds')
    }
    return ipcRenderer.invoke('embedded-browser:bounds', bounds)
  },
  close: () => ipcRenderer.invoke('embedded-browser:close'),
  inspect: () => ipcRenderer.invoke('embedded-browser:inspect'),
  onState: onEmbeddedBrowserState,
})

const electronAPI = Object.freeze({
  embeddedBrowser,
  updateStatus: () => ipcRenderer.invoke('updates:status'),
  checkForUpdates: () => ipcRenderer.invoke('updates:check'),
  installUpdate: () => ipcRenderer.invoke('updates:install'),
  onUpdateState: (listener) => {
    if (typeof listener !== 'function') throw new TypeError('Invalid update listener')
    const handler = (_event, state) => listener(state)
    ipcRenderer.on('updates:state', handler)
    return () => ipcRenderer.removeListener('updates:state', handler)
  },
  getControlSession: () => ipcRenderer.invoke('control:get-session'),
  credentialStatus: () => ipcRenderer.invoke('credentials:status'),
  setCredential: (id, value) => ipcRenderer.invoke('credentials:set', credentialId(id), safeString(value, 'credential value')),
  deleteCredential: (id) => ipcRenderer.invoke('credentials:delete', credentialId(id)),
  applyStoredCredentials: () => ipcRenderer.invoke('credentials:apply'),
  changeMcpCredential: (input) => ipcRenderer.invoke('mcp:credential', mcpCredential(input)),
  selectDirectory: (defaultPath) => ipcRenderer.invoke('dialog:select-directory', optionalString(defaultPath, 'default path')),
  setTheme: (value) => ipcRenderer.invoke('theme:set', theme(value)),
  writeClipboardText: (text) => ipcRenderer.invoke('clipboard:write-text', safeString(text, 'clipboard text')),
  windowControl: (action) => ipcRenderer.invoke('window:control', windowAction(action)),
  isWindowMaximized: () => ipcRenderer.invoke('window:is-maximized'),
  onWindowMaximized,
  clearAppCache: () => ipcRenderer.invoke('storage:clear-app-cache'),
  backendBaseUrl,
  platform: process.platform,
  isElectron: true,
})

contextBridge.exposeInMainWorld('electronAPI', electronAPI)
