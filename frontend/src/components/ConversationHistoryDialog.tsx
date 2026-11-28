import { useEffect, useRef, useState } from 'react'
import { RotateCcw, Trash2, X } from 'lucide-react'
import {
  fetchConversations, fetchDeletedConversations, permanentlyDeleteConversation, restoreConversation,
} from '../lib/api/conversations'
import type { Conversation } from '../lib/api/types'

const PAGE_SIZE = 50

interface Props {
  onClose: () => void
  onSelect: (conversation: Conversation) => void
  onRefresh?: () => void
}

/** Search saved chats and restore protected conversations without discarding Run evidence. */
export function ConversationHistoryDialog({ onClose, onSelect, onRefresh }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const requestRef = useRef<AbortController | null>(null)
  const generation = useRef(0)
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'history' | 'trash'>('history')
  const [chats, setChats] = useState<Conversation[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [restoringId, setRestoringId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [error, setError] = useState('')

  /** Discard stale searches and append pages using the last activity cursor. */
  const load = async (search: string, after?: Conversation) => {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    const request = ++generation.current
    setLoading(true)
    setError('')
    try {
      const fetchPage = view === 'trash' ? fetchDeletedConversations : fetchConversations
      const page = await fetchPage(PAGE_SIZE + 1, 0, {
        search,
        beforeUpdatedAt: after?.updated_at ?? after?.created_at,
        beforeId: after?.id,
        signal: controller.signal,
      })
      if (controller.signal.aborted || request !== generation.current) return
      setChats((current) => {
        const merged = new Map((after ? current : []).map((chat) => [chat.id, chat]))
        for (const chat of page.slice(0, PAGE_SIZE)) merged.set(chat.id, chat)
        return [...merged.values()]
      })
      setHasMore(page.length > PAGE_SIZE)
    } catch (caught) {
      if (!controller.signal.aborted && request === generation.current) {
        setError(caught instanceof Error ? caught.message : 'Could not load chat history.')
      }
    } finally {
      if (!controller.signal.aborted && request === generation.current) setLoading(false)
    }
  }

  const restore = async (conversation: Conversation) => {
    if (restoringId) return
    setRestoringId(conversation.id)
    setError('')
    try {
      await restoreConversation(conversation.id)
      setChats((current) => current.filter((chat) => chat.id !== conversation.id))
      onRefresh?.()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not restore this conversation.')
    } finally {
      setRestoringId(null)
    }
  }

  const permanentlyDelete = async (conversation: Conversation) => {
    if (deletingId || restoringId) return
    setDeletingId(conversation.id)
    setError('')
    try {
      await permanentlyDeleteConversation(conversation.id)
      setChats((current) => current.filter((chat) => chat.id !== conversation.id))
      setConfirmingDeleteId(null)
      onRefresh?.()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not permanently delete this conversation.')
    } finally {
      setDeletingId(null)
    }
  }

  useEffect(() => {
    if (!dialogRef.current?.open) dialogRef.current?.showModal()
    searchRef.current?.focus({ preventScroll: true })
    return () => requestRef.current?.abort()
  }, [])

  useEffect(() => {
    requestRef.current?.abort()
    generation.current += 1
    setChats([])
    setHasMore(false)
    const timer = window.setTimeout(() => void load(query), query ? 200 : 0)
    return () => window.clearTimeout(timer)
  // Search and view own the request generation; load reads no changing state beyond them.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, view])

  return (
    <dialog
      ref={dialogRef}
      onClose={onClose}
      aria-labelledby="chat-history-title"
      className="settings-shell m-auto w-[min(36rem,90vw)] rounded-2xl p-4 backdrop:bg-black/60"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-1">
          <button
            type="button"
            aria-pressed={view === 'history'}
            className={`st-btn min-h-8 px-3 text-xs ${view === 'history' ? 'st-btn-primary' : 'st-btn-ghost'}`}
            onClick={() => setView('history')}
          >
            History
          </button>
          <button
            type="button"
            aria-pressed={view === 'trash'}
            className={`st-btn min-h-8 px-3 text-xs ${view === 'trash' ? 'st-btn-primary' : 'st-btn-ghost'}`}
            onClick={() => setView('trash')}
          >
            Trash
          </button>
        </div>
        <button type="button" aria-label="Close chat history" className="st-btn st-btn-ghost st-btn-icon" onClick={() => dialogRef.current?.close()}>
          <X size={16} />
        </button>
      </div>
      <h2 id="chat-history-title" className="sr-only">Chat history</h2>
      {view === 'trash' && (
        <p className="st-desc mb-3 text-xs">
          Conversations with active or unresolved Runs are kept here with their recovery history. Restore one to continue using it.
        </p>
      )}
      <label htmlFor="chat-history-search" className="sr-only">Search all chats</label>
      <input
        ref={searchRef}
        id="chat-history-search"
        type="search"
        value={query}
        maxLength={200}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={view === 'trash' ? 'Search Trash' : 'Search chats, messages, or folders'}
        className="st-input mb-3 w-full"
      />
      <ul className="st-scroll max-h-[55vh] space-y-1 overflow-y-auto" aria-busy={loading}>
        {chats.map((chat) => (
          <li key={chat.id}>
            {view === 'trash' ? (
              <div className="st-nav-item flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{chat.title}</span>
                  <span className="st-hint block truncate">
                    {new Date(chat.updated_at ?? chat.created_at).toLocaleDateString()}
                    {chat.running ? ' · Run still active' : ''}
                  </span>
                </span>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    className="st-btn st-btn-ghost min-h-8 gap-1 px-2 text-xs"
                    disabled={restoringId !== null || deletingId !== null}
                    onClick={() => void restore(chat)}
                    aria-label={`Restore ${chat.title}`}
                  >
                    <RotateCcw size={13} />
                    {restoringId === chat.id ? 'Restoring…' : 'Restore'}
                  </button>
                  {confirmingDeleteId === chat.id ? (
                    <>
                      <button
                        type="button"
                        className="st-btn st-btn-ghost min-h-8 px-2 text-xs text-red-300"
                        disabled={restoringId !== null || deletingId !== null}
                        onClick={() => void permanentlyDelete(chat)}
                        aria-label={`Confirm permanent delete ${chat.title}`}
                      >
                        {deletingId === chat.id ? 'Deleting…' : 'Confirm delete'}
                      </button>
                      <button
                        type="button"
                        className="st-btn st-btn-ghost min-h-8 px-2 text-xs"
                        disabled={deletingId !== null}
                        onClick={() => setConfirmingDeleteId(null)}
                        aria-label={`Cancel permanent delete ${chat.title}`}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="st-btn st-btn-ghost st-btn-icon min-h-8"
                      disabled={restoringId !== null || deletingId !== null}
                      onClick={() => setConfirmingDeleteId(chat.id)}
                      aria-label={`Delete permanently ${chat.title}`}
                      title="Delete permanently"
                    >
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <button
                type="button"
                className="st-nav-item w-full rounded-lg px-3 py-2 text-left"
                onClick={() => { onSelect(chat); dialogRef.current?.close() }}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{chat.title}</span>
                  <span className="st-hint block truncate">
                    {new Date(chat.updated_at ?? chat.created_at).toLocaleDateString()}
                    {chat.workspace_is_default
                      ? ' · Default workspace'
                      : chat.workspace_dir ? ` · ${chat.workspace_dir}` : ''}
                  </span>
                </span>
              </button>
            )}
          </li>
        ))}
      </ul>
      {error && <p role="alert" className="py-2 text-sm text-red-300">{error}</p>}
      {!loading && !error && chats.length === 0 && (
        <p className="st-desc py-3">{view === 'trash' ? 'Trash is empty.' : 'No chats found.'}</p>
      )}
      {(hasMore || error) && (
        <button type="button" className="st-btn st-btn-ghost mt-2 min-h-9 w-full" disabled={loading} onClick={() => void load(query, chats[chats.length - 1])}>
          {loading ? 'Loading…' : error ? 'Retry' : 'Load older chats'}
        </button>
      )}
    </dialog>
  )
}
