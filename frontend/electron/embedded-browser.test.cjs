const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const main = fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8')
const preload = fs.readFileSync(path.join(__dirname, 'preload.js'), 'utf8')

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
