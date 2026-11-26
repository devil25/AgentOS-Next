export const DEFAULT_AGENT_NAME = 'AgentOS-Next'
export const LEGACY_AGENT_NAMES = new Set(['Agent', 'Monaw'])

export function resolveAgentName(value?: string | null): string {
  const trimmed = (value ?? '').trim()
  return !trimmed || LEGACY_AGENT_NAMES.has(trimmed) ? DEFAULT_AGENT_NAME : trimmed
}
