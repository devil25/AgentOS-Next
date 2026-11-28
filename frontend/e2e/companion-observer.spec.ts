import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { test, expect } from './desktop'

test('Playwright verifies companion streaming and completed-response events in Chromium', async ({ desktop }) => {
  const { app } = desktop
  const observerSource = await readFile(resolve(import.meta.dirname, '../../browser-companion/content.js'), 'utf8')
  await app.context().route('https://chatgpt.com/**', route => route.fulfill({
    status: 200,
    contentType: 'text/html',
    body: '<!doctype html><html><head><title>ChatGPT observer fixture</title></head><body><main><div data-message-author-role="user" data-message-id="user-1">Run observer test</div><div data-message-author-role="assistant" data-message-id="assistant-1">Earlier reply</div><textarea placeholder="Message"></textarea></main></body></html>',
  }))
  const providerWindow = await app.waitForEvent('window')
  const createWindow = app.evaluate(({ BrowserWindow }) => new BrowserWindow({
    show: false,
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false },
  }).id)
  const windowId = await createWindow
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
      return observations.findLast(item => item.eventType === 'assistant_streaming')
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
      return observations.findLast(item => item.eventType === 'assistant_completed')
    })
    expect(completed).toMatchObject({ provider: 'chatgpt', generating: false, stable: true })
    expect((completed?.messages as Array<Record<string, unknown>>).at(-1)).toMatchObject({
      message_id: 'assistant-stream', role: 'assistant', complete: true,
    })
    expect(Number(completed?.sequence)).toBeGreaterThan(Number(streaming?.sequence))
    expect(completed?.documentId).toBe(streaming?.documentId)
  } finally {
    await providerPage.close().catch(() => {})
    await app.evaluate(({ BrowserWindow }, id) => BrowserWindow.fromId(id)?.close(), windowId)
  }
})
