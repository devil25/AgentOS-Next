export type ConnectionPortalId = 'modelProviders' | 'webSearch' | 'telegram'

export const COMPATIBLE_API_PRESETS = {
  custom: {
    label: 'Custom / local OpenAI-compatible endpoint',
    baseUrl: '',
    modelName: 'your-model-id',
    note: 'Enter the API base URL and the exact model ID supported by your server.',
  },
  deepseek: {
    label: 'DeepSeek API · V4.1 Flash',
    baseUrl: 'https://api.deepseek.com',
    modelName: 'deepseek-flash',
    note: 'DeepSeek Flash supports tool calls and vision. Thinking mode is mapped to its API controls.',
  },
  zai: {
    label: 'Z.ai API · general resource balance',
    baseUrl: 'https://api.z.ai/api/paas/v4',
    modelName: 'glm-5.3',
    note: 'General OpenAI-compatible API endpoint. This is not the separate Coding Plan endpoint.',
  },
  zaiCoding: {
    label: 'Z.ai Coding Plan · coding endpoint',
    baseUrl: 'https://api.z.ai/api/coding/paas/v4',
    modelName: 'glm-5.4',
    note: 'Dedicated Coding Plan endpoint; intended for supported coding scenarios and plan quota.',
  },
  openrouter: {
    label: 'OpenRouter · free-model router',
    baseUrl: 'https://openrouter.ai/api/v1',
    modelName: 'openrouter/free',
    note: 'Routes to an available free model. Model availability and tool support can vary.',
  },
  ollama: {
    label: 'Ollama · local models',
    baseUrl: 'http://localhost:11434/v1',
    modelName: 'qwen3.5:4b',
    note: 'Connects to the local Ollama OpenAI-compatible API. Keep Ollama running; AgentOS-Next will discover the models installed on this machine.',
  },
  lmStudio: {
    label: 'LM Studio · local server',
    baseUrl: 'http://localhost:1234/v1',
    modelName: '',
    note: 'Start the local server in LM Studio. AgentOS-Next will discover models exposed by its OpenAI-compatible /models endpoint.',
  },
} as const

export type CompatibleApiPresetId = keyof typeof COMPATIBLE_API_PRESETS

export const COMPATIBLE_API_PRESET_OPTIONS = Object.entries(COMPATIBLE_API_PRESETS).map(([value, preset]) => ({
  value: value as CompatibleApiPresetId,
  label: preset.label,
}))

export type ConnectionSecretId =
  | 'openai'
  | 'google'
  | 'compatible'
  | 'tavily'
  | 'telegramBot'

export type ConnectionSecretValues = Record<ConnectionSecretId, string>
export type ConnectionSecretStatuses = Record<ConnectionSecretId, boolean>

export const CONNECTION_SECRET_FIELDS: Record<
  ConnectionSecretId,
  {
    label: string
    statusLabel: string
    placeholder: string
  }
> = {
  openai: {
    label: 'OpenAI API Key',
    statusLabel: 'OpenAI key saved',
    placeholder: 'OpenAI API Key',
  },
  google: {
    label: 'Google API Key',
    statusLabel: 'Google key saved',
    placeholder: 'Google API Key',
  },
  compatible: {
    label: 'OpenAI-compatible API Key',
    statusLabel: 'Compatible API key saved',
    placeholder: 'Provider API Key',
  },
  tavily: {
    label: 'Tavily API Key',
    statusLabel: 'Tavily key saved',
    placeholder: 'Tavily API Key',
  },
  telegramBot: {
    label: 'Telegram Bot Token',
    statusLabel: 'Telegram token saved',
    placeholder: 'Telegram Bot Token',
  },
}

export const CONNECTION_PORTALS: Array<{
  id: ConnectionPortalId
  label: string
  description: string
  secretIds: ConnectionSecretId[]
}> = [
  {
    id: 'modelProviders',
    label: 'Model providers',
    description: 'Connect OpenAI, Google, cloud-compatible APIs, or local Ollama and LM Studio servers.',
    secretIds: ['openai', 'google', 'compatible'],
  },
  {
    id: 'webSearch',
    label: 'Web search',
    description: 'Tavily credential for search-backed tools.',
    secretIds: ['tavily'],
  },
  {
    id: 'telegram',
    label: 'Telegram',
    description: 'Telegram bot credential and allowlist for the laptop connection bridge.',
    secretIds: ['telegramBot'],
  },
]

export const DEFAULT_CONNECTION_PORTAL: ConnectionPortalId = 'modelProviders'

export const CONNECTION_PORTAL_OPTIONS = CONNECTION_PORTALS.map((portal) => ({
  value: portal.id,
  label: portal.label,
}))
