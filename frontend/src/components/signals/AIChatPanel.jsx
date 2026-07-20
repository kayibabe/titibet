import { useState, useRef, useEffect } from 'react'
import { Send, Loader2, Bot, User, Trash2 } from 'lucide-react'
import { chatWithAdvisor } from '../../api/advisor'

function ChatBubble({ msg }) {
  const isUser = msg.role === 'user'
  return (
    <div className={`flex gap-2 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
      <div className={`shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-white text-[10px] ${
        isUser ? 'bg-[var(--accent)]' : 'bg-slate-600'
      }`}>
        {isUser ? <User size={12} /> : <Bot size={12} />}
      </div>
      <div className={`max-w-[80%] px-3 py-2 rounded-xl text-sm leading-relaxed ${
        isUser
          ? 'bg-[var(--accent)] text-white rounded-tr-sm'
          : 'bg-[var(--code-bg)] border border-[var(--border)] text-[var(--text)] rounded-tl-sm'
      }`}>
        {msg.content}
      </div>
    </div>
  )
}

export default function AIChatPanel() {
  const [history, setHistory] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, loading])

  async function send() {
    const q = input.trim()
    if (!q || loading) return
    const newHistory = [...history, { role: 'user', content: q }]
    setHistory(newHistory)
    setInput('')
    setLoading(true)
    setError(null)
    try {
      // Pass history without the latest user message (server appends it)
      const apiHistory = history.map(m => ({ role: m.role, content: m.content }))
      const { answer } = await chatWithAdvisor(q, apiHistory)
      setHistory(h => [...h, { role: 'assistant', content: answer }])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="flex flex-col h-[480px]">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-3 p-1 pr-2">
        {history.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-center opacity-60">
            <Bot size={32} className="text-[var(--accent)]" />
            <p className="text-sm text-[var(--text)]">Ask me about today's signals, a specific fixture, or any betting question.</p>
          </div>
        )}
        {history.map((msg, i) => <ChatBubble key={i} msg={msg} />)}
        {loading && (
          <div className="flex gap-2">
            <div className="w-6 h-6 rounded-full bg-slate-600 flex items-center justify-center shrink-0">
              <Bot size={12} className="text-white" />
            </div>
            <div className="bg-[var(--code-bg)] border border-[var(--border)] rounded-xl rounded-tl-sm px-3 py-2">
              <Loader2 size={14} className="animate-spin text-[var(--accent)]" />
            </div>
          </div>
        )}
        {error && (
          <p className="text-xs text-red-400 px-1">{error}</p>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t border-[var(--border)] pt-3 mt-3">
        <div className="flex gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Ask about today's picks…"
            rows={2}
            className="flex-1 resize-none rounded-lg border border-[var(--border)] bg-[var(--code-bg)] text-sm text-[var(--text)] placeholder:opacity-40 px-3 py-2 focus:outline-none focus:border-[var(--accent)] transition-colors"
          />
          <div className="flex flex-col gap-1">
            <button
              onClick={send}
              disabled={!input.trim() || loading}
              className="p-2 rounded-lg bg-[var(--accent)] text-white disabled:opacity-40 hover:opacity-90 transition-opacity"
              title="Send (Enter)"
            >
              <Send size={16} />
            </button>
            {history.length > 0 && (
              <button
                onClick={() => setHistory([])}
                title="Clear conversation"
                className="p-2 rounded-lg border border-[var(--border)] text-[var(--text)] opacity-50 hover:opacity-80 transition-opacity"
              >
                <Trash2 size={14} />
              </button>
            )}
          </div>
        </div>
        <p className="text-[10px] text-[var(--text)] opacity-35 mt-1.5">Enter to send · Shift+Enter for new line</p>
      </div>
    </div>
  )
}
