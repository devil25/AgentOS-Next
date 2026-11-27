const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const main = fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8')
const preload = fs.readFileSync(path.join(__dirname, 'preload.js'), 'utf8')
const { sendNativeKey, replaceFocusedText } = require('./embedded-browser-input.cjs')

test('embedded browser uses isolated persistent provider sessions', () => {
  assert.match(main, /new WebContentsView\(/)
  assert.match(main, /persist:agentos-embedded-\$\{provider\}/)
  assert.match(main, /contextIsolation:\s*true/)
  assert.match(main, /nodeIntegration:\s*false/)
  assert.match(main, /sandbox:\s*true/)
  assert.match(main, /webSecurity:\s*true/)
  assert.match(main, /allowRunningInsecureContent:\s*false/)
})

test('embedded browser rejects insecure and credential-bearing URLs', () => {
  assert.match(main, /parsed\.protocol !== 'https:'/)
  assert.match(main, /parsed\.username \|\| parsed\.password/)
  assert.match(main, /embeddedSafeUrl\(url\)/)
  assert.match(main, /will-navigate/)
})

test('embedded browser IPC is restricted to the trusted AgentOS renderer', () => {
  for (const channel of ['open', 'navigate', 'back', 'forward', 'reload', 'bounds', 'close', 'inspect']) {
    assert.match(main, new RegExp(`ipcMain\\.handle\\('embedded-browser:${channel}'`))
  }
  assert.match(preload, /embeddedBrowser,/)
  assert.match(preload, /embeddedProvider\(provider\)/)
  assert.match(preload, /embeddedUrl\(url\)/)
  assert.match(preload, /onEmbeddedBrowserState/)
  assert.match(main, /assertTrustedIpcSender\(event\)/)
})

test('embedded page inspection is fixed read-only extraction, not arbitrary script IPC', () => {
  assert.match(main, /embedded-browser:inspect/)
  assert.match(main, /document\.body\?\.innerText/)
  assert.match(main, /querySelectorAll\('h1,h2'\)/)
  assert.doesNotMatch(preload, /executeJavaScript|webContents|debugger/)
  assert.doesNotMatch(main, /ipcMain\.handle\('embedded-browser:execute/)
})

test('embedded browser denies site permission prompts by default', () => {
  assert.match(main, /setPermissionRequestHandler\(\(_contents, _permission, callback\) => callback\(false\)\)/)
})

test('backend page-read bridge is loopback-only and bearer-authenticated', () => {
  assert.match(main, /embeddedBrowserBridgeServer = http\.createServer/)
  assert.match(main, /embeddedBrowserBridgeServer\.listen\(0, '127\.0\.0\.1'/)
  assert.match(main, /request\.socket\.remoteAddress/)
  assert.match(main, /crypto'\)\.timingSafeEqual/)
  assert.match(main, /\['\/v1\/inspect', '\/v1\/action', '\/v1\/conversation'\]\.includes\(request\.url\)/)
  assert.match(main, /AGENTOS_EMBEDDED_BROWSER_SECRET: EMBEDDED_BROWSER_BRIDGE_SECRET/)
  assert.match(main, /await startEmbeddedBrowserBridge\(\)/)
})

test('embedded browser exposes only fixed interaction actions through its authenticated bridge', () => {
  assert.match(main, /const EMBEDDED_BROWSER_INTERACTION_SCRIPT/)
  for (const action of ['snapshot', 'click', 'type', 'press_key']) {
    assert.match(main, new RegExp(`'${action}'`))
  }
  assert.ok(main.includes("if (!['Enter','Escape','Tab','ArrowUp','ArrowDown','Backspace'].includes(key))"))
  assert.match(main, /input:not\(\[type="password"\]\)/)
  assert.match(main, /payload\.action === 'navigate'/)
  assert.match(main, /embeddedSafeUrl\(payload\.url\)/)
  assert.match(main, /payload\.text/)
  assert.match(main, /value\.length > 4000/)
  assert.doesNotMatch(main, /payload\.script|payload\.javascript|executeJavaScript\(payload/)
})

test('embedded conversation endpoint returns provider-aware structured messages', () => {
  assert.match(main, /action === 'conversation'/)
  assert.match(main, /data-message-author-role/)
  assert.match(main, /composerReady/)
  assert.match(main, /loginRequired/)
  assert.match(main, /generating/)
  assert.match(main, /request\.url === '\/v1\/conversation'/)
  assert.match(main, /executeJavaScript\(script, true\)/)
})

test('embedded browser text entry and Enter use native Electron input events', () => {
  const events = []
  const webContents = {
    sendInputEvent: (event) => events.push({ kind: 'input', ...event }),
    insertText: (text) => events.push({ kind: 'text', text }),
  }

  replaceFocusedText(webContents, 'hello', 'win32')
  sendNativeKey(webContents, 'Enter')

  assert.deepEqual(events, [
    { kind: 'input', type: 'keyDown', keyCode: 'A', modifiers: ['control'] },
    { kind: 'input', type: 'keyUp', keyCode: 'A', modifiers: ['control'] },
    { kind: 'input', type: 'keyDown', keyCode: 'Backspace' },
    { kind: 'input', type: 'keyUp', keyCode: 'Backspace' },
    { kind: 'text', text: 'hello' },
    { kind: 'input', type: 'keyDown', keyCode: 'Enter' },
    { kind: 'input', type: 'keyUp', keyCode: 'Enter' },
  ])
  assert.match(main, /replaceFocusedText\(webContents, value, process\.platform\)/)
  assert.match(main, /sendNativeKey\(webContents, key\)/)
  assert.doesNotMatch(main, /target\.dispatchEvent\(new KeyboardEvent/)
})

test('embedded browser native text replacement uses the macOS command modifier', () => {
  const events = []
  const webContents = {
    sendInputEvent: (event) => events.push(event),
    insertText: () => {},
  }
  replaceFocusedText(webContents, 'test', 'darwin')
  assert.deepEqual(events.slice(0, 2), [
    { type: 'keyDown', keyCode: 'A', modifiers: ['meta'] },
    { type: 'keyUp', keyCode: 'A', modifiers: ['meta'] },
  ])
})
