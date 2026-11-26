import { expect, it } from 'vitest'
import { COMPATIBLE_API_PRESETS } from './connectionPortalConfig'

it('provides local OpenAI-compatible presets for Ollama and LM Studio', () => {
  expect(COMPATIBLE_API_PRESETS.ollama).toMatchObject({
    baseUrl: 'http://localhost:11434/v1',
    modelName: 'qwen3.5:4b',
  })
  expect(COMPATIBLE_API_PRESETS.lmStudio).toMatchObject({
    baseUrl: 'http://localhost:1234/v1',
  })
  expect(COMPATIBLE_API_PRESETS.ollama.note).toMatch(/discover/i)
  expect(COMPATIBLE_API_PRESETS.lmStudio.note).toMatch(/\/models/)
})
