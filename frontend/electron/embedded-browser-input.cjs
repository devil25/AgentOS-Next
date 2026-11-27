'use strict'

const ALLOWED_KEYS = new Set(['Enter', 'Escape', 'Tab', 'ArrowUp', 'ArrowDown', 'Backspace'])

function sendNativeKey(webContents, key) {
  if (!ALLOWED_KEYS.has(key)) throw new Error('Unsupported key')
  webContents.sendInputEvent({ type: 'keyDown', keyCode: key })
  webContents.sendInputEvent({ type: 'keyUp', keyCode: key })
}

function replaceFocusedText(webContents, text, platform = process.platform, append = false) {
  if (typeof text !== 'string' || text.length > 4000) {
    throw new Error('Text is invalid or too long (maximum 4000 characters)')
  }
  if (!append) {
    const modifier = platform === 'darwin' ? 'meta' : 'control'
    webContents.sendInputEvent({ type: 'keyDown', keyCode: 'A', modifiers: [modifier] })
    webContents.sendInputEvent({ type: 'keyUp', keyCode: 'A', modifiers: [modifier] })
    sendNativeKey(webContents, 'Backspace')
  }
  if (text) webContents.insertText(text)
  return { characters: text.length }
}

module.exports = { sendNativeKey, replaceFocusedText }
