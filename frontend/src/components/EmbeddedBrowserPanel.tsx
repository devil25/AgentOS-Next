import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, BookOpen, Globe, Loader2, RefreshCw, X } from 'lucide-react'

type Provider = 'chatgpt' | 'gemini' | 'deepseek' | 'zai'
type BrowserState = {
  open: boolean
  provider: string | null
  url: string
  title: string
  loading: boolean
  canGoBack: boolean
  canGoForward: boolean
  error?: string
}
type Inspection = { title: string; url: string; text: string; headings: string[]; inspectedAt: string }

const PROVIDERS: { id: Provider; label: string; url: string }[] = [
  { id: 'chatgpt', label: 'ChatGPT', url: 'https://chatgpt.com/' },
  { id: 'gemini', label: 'Gemini', url: 'https://gemini.google.com/app' },
  { id: 'deepseek', label: 'DeepSeek', url: 'https://chat.deepseek.com/' },
  { id: 'zai', label: 'Z.ai', url: 'https://chat.z.ai/' },
]

export function EmbeddedBrowserPanel({ onClose, initialProvider = 'chatgpt' }: { onClose: () => void; initialProvider?: Provider }) {
  const viewportRef = useRef<HTMLDivElement>(null)
  const [provider, setProvider] = useState<Provider>(initialProvider)
  const [address, setAddress] = useState(PROVIDERS.find((item) => item.id === initialProvider)?.url || 'https://chatgpt.com/')
  const [state, setState] = useState<BrowserState>({
    open: false, provider: null, url: '', title: '', loading: true, canGoBack: false, canGoForward: false,
  })
  const [inspection, setInspection] = useState<Inspection | null>(null)
  const [error, setError] = useState('')
  const api = typeof window !== 'undefined' ? window.electronAPI?.embeddedBrowser : undefined

  const updateBounds = useCallback(() => {
    const element = viewportRef.current
    if (!element || !api) return
    const rect = element.getBoundingClientRect()
    void api.setBounds({
      x: rect.left,
      y: rect.top,
      width: rect.width,
      height: Math.max(0, rect.height),
    }).catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not resize the browser'))
  }, [api, inspection])

  useEffect(() => {
    if (!api) {
      setError('The embedded browser is available in the desktop application.')
      return
    }
    const stop = api.onState((next) => {
      setState(next)
      if (next.url) setAddress(next.url)
      if (next.error) setError(next.error)
    })
    return () => {
      stop()
      void api.close().catch(() => {})
    }
  }, [api])

  useEffect(() => {
    setProvider(initialProvider)
    setAddress(PROVIDERS.find((item) => item.id === initialProvider)?.url || '')
    setInspection(null)
    if (!api) return
    void api.open(initialProvider).catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not open browser'))
  }, [api, initialProvider])

  useEffect(() => {
    updateBounds()
    const element = viewportRef.current
    if (!element) return
    const observer = new ResizeObserver(updateBounds)
    observer.observe(element)
    window.addEventListener('resize', updateBounds)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', updateBounds)
    }
  }, [updateBounds])

  const openProvider = async (next: Provider) => {
    if (!api) return
    setProvider(next)
    setAddress(PROVIDERS.find((item) => item.id === next)?.url || '')
    setInspection(null)
    setError('')
    try { await api.open(next) } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not open provider')
    }
  }

  const navigate = async () => {
    if (!api) return
    const value = address.trim()
    if (!/^https:\/\//i.test(value)) {
      setError('Use a complete HTTPS address.')
      return
    }
    setError('')
    setInspection(null)
    try { await api.navigate(value) } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Navigation failed')
    }
  }

  const inspect = async () => {
    if (!api) return
    setError('')
    try { setInspection(await api.inspect()) } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Page reading failed')
    }
  }

  return (
    <section className="fixed inset-0 z-[100] flex flex-col bg-[#101713] text-slate-100" aria-label="Embedded browser">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-white/10 bg-[#17221b] px-3">
        <Globe size={17} className="text-emerald-300" />
        <strong className="mr-2 text-sm">AgentOS Browser</strong>
        <select
          value={provider}
          onChange={(event) => { void openProvider(event.target.value as Provider) }}
          className="h-8 rounded-md border border-white/10 bg-[#0d1510] px-2 text-xs outline-none focus:border-emerald-400"
          aria-label="AI website"
        >
          {PROVIDERS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
        <div className="ml-auto flex items-center gap-1">
          <button type="button" onClick={() => { void api?.back() }} disabled={!state.canGoBack} className="rounded p-2 hover:bg-white/10 disabled:opacity-30" aria-label="Back"><ArrowLeft size={16} /></button>
          <button type="button" onClick={() => { void api?.forward() }} disabled={!state.canGoForward} className="rounded p-2 hover:bg-white/10 disabled:opacity-30" aria-label="Forward"><ArrowRight size={16} /></button>
          <button type="button" onClick={() => { void api?.reload() }} className="rounded p-2 hover:bg-white/10" aria-label="Reload"><RefreshCw size={15} /></button>
          <button type="button" onClick={() => { void inspect() }} className="flex items-center gap-1 rounded-md border border-emerald-500/30 px-2 py-1.5 text-xs text-emerald-200 hover:bg-emerald-500/10"><BookOpen size={14} /> Read page</button>
          <button type="button" onClick={onClose} className="rounded p-2 hover:bg-red-500/15" aria-label="Close browser"><X size={17} /></button>
        </div>
      </header>
      <form className="flex h-11 shrink-0 items-center gap-2 border-b border-white/10 bg-[#131d17] px-3" onSubmit={(event) => { event.preventDefault(); void navigate() }}>
        <input value={address} onChange={(event) => setAddress(event.target.value)} aria-label="Website address"
          className="h-8 min-w-0 flex-1 rounded-md border border-white/10 bg-[#0b120e] px-3 text-xs text-slate-200 outline-none focus:border-emerald-400" />
        <button type="submit" className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium hover:bg-emerald-500">Go</button>
        {state.loading && <Loader2 size={15} className="animate-spin text-emerald-300" aria-label="Loading" />}
      </form>
      <div ref={viewportRef} className="min-h-0 flex-1 bg-white" aria-label="Website viewport" />
      {inspection && (
        <div className="h-[220px] shrink-0 overflow-auto border-t border-emerald-500/30 bg-[#0c1510] p-3 text-xs">
          <div className="mb-2 flex items-center justify-between">
            <div className="min-w-0"><strong>{inspection.title || 'Page text'}</strong><p className="truncate text-slate-400">{inspection.url}</p></div>
            <button type="button" onClick={() => setInspection(null)} className="rounded p-1 hover:bg-white/10" aria-label="Close page text"><X size={15} /></button>
          </div>
          {inspection.headings.length > 0 && <p className="mb-2 text-emerald-200">{inspection.headings.join(' · ')}</p>}
          <pre className="whitespace-pre-wrap break-words font-sans leading-relaxed text-slate-300">{inspection.text || 'No visible page text was found.'}</pre>
          <p className="mt-2 text-[10px] text-slate-500">Read directly from the embedded page · {new Date(inspection.inspectedAt).toLocaleTimeString()}</p>
        </div>
      )}
      {error && <div role="alert" className="absolute bottom-3 left-1/2 z-10 max-w-[80%] -translate-x-1/2 rounded-lg border border-red-400/30 bg-[#351b1b] px-3 py-2 text-xs text-red-100 shadow-lg">{error}</div>}
    </section>
  )
}
