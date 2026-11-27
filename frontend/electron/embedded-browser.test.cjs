const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const vm = require('node:vm')

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
  assert.match(main, /\/v1\/conversation/)
  assert.match(main, /\/v1\/companion\/event/)
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

test('embedded page scripts run in an isolated world and preserve actionable execution errors', () => {
  assert.match(main, /const EMBEDDED_BROWSER_ISOLATED_WORLD_ID = 1337/)
  assert.match(main, /executeJavaScriptInIsolatedWorld\(/)
  assert.match(main, /__agentosScriptOk/)
  assert.match(main, /provider page execution context changed/)
  assert.match(main, /browser_execution_context_changed/)
  assert.doesNotMatch(main, /webContents\.executeJavaScript\(/)
})

test('embedded conversation endpoint returns provider-aware structured messages', () => {
  assert.match(main, /action === 'conversation'/)
  assert.match(main, /data-message-author-role/)
  assert.match(main, /composerReady/)
  assert.match(main, /loginRequired/)
  assert.match(main, /generating/)
  assert.match(main, /request\.url === '\/v1\/conversation'/)
  assert.match(main, /executeEmbeddedBrowserScript\(embeddedBrowserView\.webContents, script\)/)
})

test('embedded ChatGPT conversation detection recognizes app-shell turn roles and safe fallbacks', () => {
  assert.match(main, /article,\[data-testid\^="conversation-turn-"\]/)
  assert.match(main, /data-turn="user"/)
  assert.match(main, /data-turn="assistant"/)
  assert.match(main, /data-markdown-text-style="assistant-message"/)
  assert.match(main, /node\.querySelector\(roleMarkerSelector\)/)
  assert.match(main, /button\[data-testid\*="stop"\]/)
  assert.match(main, /data-is-streaming/)
  assert.match(main, /const contextLimit = \/conversation is too long/)
  assert.match(main, /contextLimit, blockedReason/)
  assert.doesNotMatch(main, /document\.querySelector\('\[aria-busy="true",\[data-testid\*="stop"/)
})

test('ChatGPT response capture uses the complete legacy-style accessibility tree', () => {
  const start = main.indexOf('async function inspectEmbeddedAccessibilityMessages')
  const end = main.indexOf('function startEmbeddedBrowserBridge', start)
  const capture = main.slice(start, end)
  assert.match(capture, /Accessibility\.getFullAXTree/)
  assert.match(capture, /maxDepth: 30/)
  assert.match(capture, /Accessibility snapshot exceeded its 3\.5s budget/)
  assert.match(capture, /Promise\.race/)
  assert.ok(capture.includes('const rawNodes = Array.isArray(tree?.nodes) ? tree.nodes : []'))
  assert.doesNotMatch(capture, /slice\(0, 4000\)/)
  assert.match(capture, /messages\.length >= 100/)
  assert.match(capture, /hasAssistantControls/)
  assert.match(capture, /copy\|read aloud\|good response\|bad response\|regenerate/)
  assert.match(capture, /ancestorHint/)
  assert.match(capture, /roleDescription/)
  assert.match(capture, /backendDOMNodeId \|\| node\.nodeId/)
  assert.doesNotMatch(capture, /DOM\.resolveNode|Runtime\.callFunctionOn/)
  assert.match(main, /snapshot\.captureSource = domHasAssistant/)
  assert.match(main, /snapshot\.messages = mergedMessages/)
  assert.match(main, /const messageKey/)
  assert.match(main, /hasAssistantMessage/)
  assert.ok(main.includes('dom+accessibility_tree'))

})

test('ChatGPT Enter confirms a newly rendered user turn or a new assistant reply', () => {
  assert.match(main, /button\[data-testid="send-button"\]/)
  assert.match(main, /button\[aria-label\*="Send" i\]/)
  assert.match(main, /send\.click\(\)/)
  assert.match(main, /submissionVerified: true/)
  assert.match(main, /chatgpt_submission_unconfirmed/)
  assert.match(main, /composerEmpty: Boolean\(composer\) && !value/)
  assert.match(main, /submittedTextPrefix: expectedPrefix/)
  assert.match(main, /baselineUserSignatures/)
  assert.match(main, /baselineAssistantTexts/)
  assert.match(main, /getAttribute\('data-testid'\) === 'prompt-textarea'/)
  assert.match(main, /const populated = editable\.filter\(element => readValue\(element\)\)/)
  assert.match(main, /composer\.closest\('form'\)/)
  assert.match(main, /baselineUserSignatures/)
  assert.match(main, /baselineAssistantTexts/)
  assert.match(main, /state\?\.userMessageVisible \|\| state\?\.assistantMessageVisible/)
  assert.match(main, /new_assistant_reply/)
  assert.match(main, /Date\.now\(\) \+ 15000/)
  assert.doesNotMatch(main, /did not clear its composer after the send attempt/)
})

test('ChatGPT accessibility capture excludes editable composer subtrees and weak role hints', () => {
  const start = main.indexOf('async function inspectEmbeddedAccessibilityMessages')
  const end = main.indexOf('function startEmbeddedBrowserBridge', start)
  const capture = main.slice(start, end)
  assert.match(capture, /hasEditableDescendant/)
  assert.match(capture, /\['textbox', 'searchbox', 'combobox', 'textfield'\]/)
  assert.match(capture, /hasExplicitUserContext/)
  assert.match(capture, /hasExplicitAssistantContext/)
  assert.doesNotMatch(capture, /userPrefix|assistantPrefix/)
})

test('embedded browser type resolves a fresh unique composer instead of trusting a stale snapshot index', () => {
  const start = main.indexOf('const EMBEDDED_BROWSER_INTERACTION_SCRIPT')
  const end = main.indexOf('`', start + 'const EMBEDDED_BROWSER_INTERACTION_SCRIPT = `'.length)
  const script = main.slice(start, end)
  assert.match(script, /if \(action === 'type'\) \{[\s\S]*?querySelectorAll\('textarea,\[contenteditable="true"\],\[role="textbox"\]'\)/)
  assert.match(script, /const preferred = composers\.filter\(element =>[\s\S]*?data-testid.*prompt-textarea/)
  assert.match(script, /if \(choices\.length !== 1\)/)
  assert.match(script, /Multiple editable composers are visible; no text was entered/)
  assert.match(script, /No visible editable text composer was found; no text was entered/)
  assert.match(script, /const index = Number\(args\.index\)/)
  assert.ok(script.indexOf("if (action === 'type')") < script.indexOf('const index = Number(args.index)'))
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
  assert.match(main, /replaceFocusedText\(webContents, value, process\.platform, payload\.append === true\)/)
  assert.match(main, /sendNativeKey\(webContents, key\)/)
  assert.doesNotMatch(main, /target\.dispatchEvent\(new KeyboardEvent/)
})

test('embedded browser appends subsequent prompt chunks without clearing earlier text', () => {
  const events = []
  const webContents = {
    sendInputEvent: (event) => events.push(event),
    insertText: (text) => events.push({ type: 'text', text }),
  }
  replaceFocusedText(webContents, 'second chunk', 'win32', true)
  assert.deepEqual(events, [{ type: 'text', text: 'second chunk' }])
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


test('closing the login browser hides it without collapsing or destroying the signed-in provider session', () => {
  assert.match(main, /function hideEmbeddedBrowser\(\)/)
  assert.match(main, /embeddedBrowserVisible = false/)
  assert.match(main, /embeddedBrowserView\.setVisible\(false\)/)
  assert.match(main, /embedded-browser:close'[\s\S]*?hideEmbeddedBrowser\(\)/)
  assert.match(main, /persist:agentos-embedded-\$\{provider\}/)
  const hide = main.slice(main.indexOf('function hideEmbeddedBrowser'), main.indexOf('function closeEmbeddedBrowser'))
  assert.doesNotMatch(hide, /\.close\(|\.destroy\(|removeChildView/)
  assert.match(hide, /embeddedBrowserView\.setVisible\(false\)/)
})

test('AgentOS starts a responsive hidden ChatGPT session at application startup', () => {
  assert.match(main, /attachEmbeddedBrowser\('chatgpt', \{ visible: false \}\)/)
  assert.match(main, /view\.webContents\.setBackgroundThrottling\(false\)/)
  assert.match(main, /view\.setBounds\(\{ x: 0, y: 96, width, height: Math\.max\(1, height - 96\) \}\)/)
  assert.match(main, /view\.setVisible\(visible\)/)
})

test('sending a web-chat message does not open or foreground the embedded browser', () => {
  const app = fs.readFileSync(path.join(__dirname, '..', 'src', 'App.tsx'), 'utf8')
  const handleSend = app.slice(app.indexOf('const handleSend ='), app.indexOf('// A new chat gets its id'))
  assert.match(handleSend, /setEmbeddedBrowserProvider\(webChatTarget\.provider\)/)
  assert.doesNotMatch(handleSend, /setShowEmbeddedBrowser\(true\)|embedded\.open\(/)
})


test('ChatGPT capture falls back to visible main-region evidence when AX messages are unavailable', () => {
  const start = main.indexOf('async function inspectEmbeddedAccessibilityMessages')
  const end = main.indexOf('function startEmbeddedBrowserBridge', start)
  const capture = main.slice(start, end)
  assert.ok(capture.includes('const rawNodes = Array.isArray(tree?.nodes) ? tree.nodes : []'))
  assert.doesNotMatch(capture, /slice\(0, 4000\)/)
  assert.ok(capture.includes('current.parentId'))
  assert.match(capture, /roledescription/)
  assert.match(main, /contentRoot = document\.querySelector\('main,\[role="main"\]'\)/)
  assert.match(main, /visible_page_text_fallback/)
  assert.match(main, /snapshot\.pageText = String\(pageEvidence\?\.text \|\| ''\)\.slice\(0, 12000\)/)
})


test('embedded companion extension is packaged and loaded into each persistent provider session', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../../browser-companion/manifest.json'), 'utf8'))
  const packageJson = JSON.parse(fs.readFileSync(path.join(__dirname, '../package.json'), 'utf8'))
  assert.equal(manifest.manifest_version, 3)
  assert.ok(manifest.content_scripts.some(item => item.matches.includes('https://chatgpt.com/*')))
  assert.ok(manifest.content_scripts.some(item => item.matches.includes('https://gemini.google.com/*')))
  assert.ok(packageJson.build.extraResources.some(item => item.to === 'browser-companion'))
  assert.match(main, /browserSession\.extensions/)
  assert.match(main, /ensureEmbeddedCompanion\(browserSession\)/)
  assert.match(main, /embeddedCompanionLoadedSessions/)
})

test('background companion observations are authenticated, provider-bound, and size-limited', () => {
  assert.match(main, /\/v1\/companion\/event/)
  assert.match(main, /payload\.provider !== currentProvider/)
  assert.match(main, /new URL\(offeredUrl\)\.origin !== new URL\(currentUrl\)\.origin/)
  assert.match(main, /const maxBodyLength = companionEvent \? 256 \* 1024/)
  assert.match(main, /captureSource: 'extension_companion'/)
})

test('ChatGPT send confirmation uses a newly rendered user turn or assistant reply, not composer clearing', () => {
  assert.match(main, /if \(state\?\.userMessageVisible \|\| state\?\.assistantMessageVisible\)/)
  assert.match(main, /composerEmpty: Boolean\(state\.composerEmpty\)/)
  assert.doesNotMatch(main, /if \(state\?\.composerFound && state\?\.composerEmpty && state\?\.userMessageVisible\)/)
})


test('conversation capture waits for an assistant reply after the exact submitted user message', () => {
  assert.match(main, /embeddedBrowserPendingSubmission/)
  assert.match(main, /messages\.findLastIndex\(item =>/)
  assert.match(main, /index > userIndex && item\?\.role === 'assistant'/)
  assert.match(main, /snapshot\.pendingSubmission = true/)
  assert.match(main, /embeddedBrowserPendingSubmission = null/)
})


test('companion service worker and provider observer are valid JavaScript', () => {
  const worker = fs.readFileSync(path.join(__dirname, '../../browser-companion/service_worker.js'), 'utf8')
  const content = fs.readFileSync(path.join(__dirname, '../../browser-companion/content.js'), 'utf8')
  assert.doesNotThrow(() => new vm.Script(worker))
  assert.doesNotThrow(() => new vm.Script(content))
  assert.match(content, /new MutationObserver\(schedule\)/)
  assert.match(content, /stableForMs/)
  assert.match(worker, /\/v1\/companion\/event/)
  assert.doesNotMatch(worker + content, /chrome\.cookies|document\.cookie|input\[type=["']password["']\]\.value/)
})


test('companion role detection supports ChatGPT app-shell user and assistant turns', () => {
  const content = fs.readFileSync(path.join(__dirname, '../../browser-companion/content.js'), 'utf8')
  assert.match(content, /node\.querySelector\(marker\) \|\| node/)
  assert.match(content, /data-turn="user"/)
  assert.match(content, /data-turn="assistant"/)
  assert.match(content, /data-markdown-text-style="assistant-message"/)
  assert.match(content, /user-query/)
  assert.match(content, /model-response/)
})
