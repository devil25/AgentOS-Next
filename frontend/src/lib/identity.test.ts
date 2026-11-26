import { describe, expect, it } from 'vitest'
import { DEFAULT_AGENT_NAME, LEGACY_AGENT_NAMES, resolveAgentName } from './identity'

describe('AgentOS-Next identity', () => {
  it('uses AgentOS-Next as the default identity', () => {
    expect(DEFAULT_AGENT_NAME).toBe('AgentOS-Next')
    expect(resolveAgentName()).toBe('AgentOS-Next')
    expect(resolveAgentName(null)).toBe('AgentOS-Next')
    expect(resolveAgentName('   ')).toBe('AgentOS-Next')
  })

  it('maps legacy product labels to the new identity', () => {
    expect(LEGACY_AGENT_NAMES.has('Monaw')).toBe(true)
    expect(resolveAgentName('Monaw')).toBe('AgentOS-Next')
    expect(resolveAgentName('Agent')).toBe('AgentOS-Next')
  })

  it('preserves custom agent names', () => {
    expect(resolveAgentName('Research Assistant')).toBe('Research Assistant')
  })
})
