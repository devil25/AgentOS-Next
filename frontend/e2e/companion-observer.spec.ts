import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { test, expect } from './desktop'

test('Playwright verifies companion streaming and completed-response events in Chromium', async ({ desktop }) => {
  const { app } = desktop
  const observerSource = await readFile(resolve(import.meta.dirname, '../../browser-companion/content.js'), 'utf8')
  await app.context().route('https://chatgpt.com/**', route => route.fulfill({
    status: 200,
    contentType: 'text/html',
    body: '<!doctype html><html><head><title>ChatGPT observer fixture</title></head><body><main><div data-message-author-role="user" data-message-id="user-1">Run observer test</div><div data-message-author-role="assistant" data-message-id="assistant-1"><div data-markdown-text-style="assistant-message">Earlier reply</div></div><textarea placeholder="Message"></textarea></main></body></html>',
  }))
  const providerWindow = app.waitForEvent('window')
  const windowId = await app.evaluate(({ BrowserWindow }) => {
    const window = new BrowserWindow({
      show: false,
      webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false },
    })
    // Electron's Playwright adapter exposes a Page after the BrowserWindow
    // creates its renderer; a newly constructed, never-navigated window has
    // no renderer Page for waitForEvent('window') to resolve.
    void window.loadURL('about:blank')
    return window.id
  })
  const providerPage = await providerWindow
  try {
    await providerPage.addInitScript(() => {
      const target = window as Window & {
        __agentosObservations?: Array<Record<string, unknown>>
        chrome?: { runtime?: { sendMessage?: (message: { snapshot?: Record<string, unknown> }) => Promise<unknown> } }
      }
      target.__agentosObservations = []
      const chromeObject = target.chrome || {}
      chromeObject.runtime = {
        sendMessage: async message => {
          target.__agentosObservations!.push(message.snapshot || {})
          return { ok: true, accepted: true }
        },
      }
      target.chrome = chromeObject
    })
    await providerPage.goto('https://chatgpt.com/c/agentos-observer-fixture')
    await providerPage.addScriptTag({ content: observerSource })
    await expect.poll(async () => providerPage.evaluate(() =>
      (window as Window & { __agentosObservations?: unknown[] }).__agentosObservations?.length || 0
    )).toBeGreaterThan(0)
    const initial = await providerPage.evaluate(() => {
      const observations = (window as Window & { __agentosObservations?: Array<Record<string, unknown>> }).__agentosObservations || []
      return observations[0]
    })
    expect(initial?.messages).toHaveLength(2)
    await providerPage.evaluate(() => {
      const assistant = document.createElement('div')
      assistant.setAttribute('data-message-author-role', 'assistant')
      assistant.setAttribute('data-message-id', 'assistant-stream')
      assistant.setAttribute('data-is-streaming', 'true')
      assistant.textContent = 'The model is writing'
      document.querySelector('main')!.append(assistant)
    })
    await expect.poll(async () => providerPage.evaluate(() => {
      const observations = (window as Window & { __agentosObservations?: Array<Record<string, unknown>> }).__agentosObservations || []
      return observations.some(item => item.eventType === 'assistant_streaming')
    })).toBe(true)
    const streaming = await providerPage.evaluate(() => {
      const observations = (window as Window & { __agentosObservations?: Array<Record<string, unknown>> }).__agentosObservations || []
      return observations.filter(item => item.eventType === 'assistant_streaming').at(-1)
    })
    expect(streaming).toMatchObject({ provider: 'chatgpt', generating: true })
    expect((streaming?.messages as Array<Record<string, unknown>>).at(-1)).toMatchObject({
      message_id: 'assistant-stream', role: 'assistant', complete: false,
    })
    await providerPage.evaluate(() => {
      const assistant = document.querySelector('[data-message-id="assistant-stream"]')!
      assistant.textContent = 'The model has finished writing'
      assistant.removeAttribute('data-is-streaming')
    })
    await expect.poll(async () => providerPage.evaluate(() => {
      const observations = (window as Window & { __agentosObservations?: Array<Record<string, unknown>> }).__agentosObservations || []
      return observations.some(item => item.eventType === 'assistant_completed')
    }), { timeout: 10_000 }).toBe(true)
    const completed = await providerPage.evaluate(() => {
      const observations = (window as Window & { __agentosObservations?: Array<Record<string, unknown>> }).__agentosObservations || []
      return observations.filter(item => item.eventType === 'assistant_completed').at(-1)
    })
    expect(completed).toMatchObject({ provider: 'chatgpt', generating: false, stable: true })
    expect((completed?.messages as Array<Record<string, unknown>>).at(-1)).toMatchObject({
      message_id: 'assistant-stream', role: 'assistant', complete: true,
    })
    expect(Number(completed?.sequence)).toBeGreaterThan(Number(streaming?.sequence))
    expect(completed?.documentId).toBe(streaming?.documentId)
    await providerPage.evaluate(() => {
      const alert = document.createElement('div')
      alert.setAttribute('role', 'alert')
      alert.textContent = 'The message you submitted was too long, please edit and resubmit.'
      document.body.append(alert)
    })
    await expect.poll(async () => providerPage.evaluate(() => {
      const observations = (window as Window & { __agentosObservations?: Array<Record<string, unknown>> }).__agentosObservations || []
      return observations.some(item => String(item.blockedReason || '').includes('too long'))
    })).toBe(true)
  } finally {
    await providerPage.close().catch(() => {})
    await app.evaluate(({ BrowserWindow }, id) => BrowserWindow.fromId(id)?.close(), windowId)
  }
})
