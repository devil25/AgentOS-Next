export {}

type CredentialId = 'openai' | 'google' | 'compatible' | 'tavily' | 'telegramBot'
type CredentialStatus = Record<CredentialId, boolean>

declare global {
  type UpdateState = {
    currentVersion: string
    status: 'unavailable' | 'idle' | 'checking' | 'up-to-date' | 'downloading' | 'ready' | 'error'
    latestVersion?: string
    progress?: number
    error?: string
  }
  interface Window {
    electronAPI?: {
      updateStatus?: () => Promise<UpdateState>
      checkForUpdates?: () => Promise<UpdateState>
      installUpdate?: () => Promise<boolean>
      onUpdateState?: (listener: (state: UpdateState) => void) => () => void
      getControlSession: () => Promise<{ token: string; expiresAt: number }>
      credentialStatus: () => Promise<CredentialStatus>
      setCredential: (id: CredentialId, value: string) => Promise<CredentialStatus>
      deleteCredential: (id: CredentialId) => Promise<CredentialStatus>
      applyStoredCredentials: () => Promise<CredentialStatus>
      changeMcpCredential: (input: {
        action: 'migrate' | 'replace' | 'delete'
        binding: { server_id: string; transport: 'stdio' | 'streamable_http'; target: 'env' | 'headers'; key: string }
        value?: string
      }) => Promise<{ ok: boolean }>
      selectDirectory?: (defaultPath?: string) => Promise<string>
      setTheme?: (theme: 'dark' | 'light') => Promise<void>
      writeClipboardText?: (text: string) => Promise<void>
      windowControl?: (action: 'minimize' | 'toggle-maximize' | 'close') => Promise<boolean>
      isWindowMaximized?: () => Promise<boolean>
      /** Returns a function that stops listening. */
      onWindowMaximized?: (listener: (maximized: boolean) => void) => () => void
      /** Clears the window's HTTP, code and shader caches. */
      clearAppCache?: () => Promise<boolean>
      embeddedBrowser?: {
        open: (provider: 'chatgpt' | 'gemini' | 'deepseek' | 'zai') => Promise<boolean>
        navigate: (url: string) => Promise<boolean>
        back: () => Promise<boolean>
        forward: () => Promise<boolean>
        reload: () => Promise<boolean>
        setBounds: (bounds: { x: number; y: number; width: number; height: number }) => Promise<boolean>
        close: () => Promise<boolean>
        inspect: () => Promise<{ title: string; url: string; text: string; headings: string[]; inspectedAt: string }>
        onState: (listener: (state: {
          open: boolean; provider: string | null; url: string; title: string; loading: boolean
          canGoBack: boolean; canGoForward: boolean; error?: string
        }) => void) => () => void
      }
      backendBaseUrl: string
      platform?: string
      isElectron: boolean
    }
  }
}
