import { useState, useEffect, useRef } from 'react'
import CommandCard from './CommandCard'
import ApprovalCard from './ApprovalCard'

export default function ConversationFeed({ runs, chain, tailRun, onApprove, onDeny, onSubmit }) {
  const [prompt, setPrompt] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const feedRef = useRef(null)

  const tailStatus = tailRun?.status
  const isDisabled = submitting || tailStatus === 'running' || tailStatus === 'waiting_approval'

  let placeholder = 'Ask the agent something\u2026'
  if (tailStatus === 'waiting_approval') placeholder = 'Waiting for approval\u2026'
  else if (tailStatus === 'running' || submitting) placeholder = 'Agent is working\u2026'
  else if (chain.length > 0) placeholder = 'Continue this chat\u2026'

  // Scroll to bottom whenever conversation updates
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight
    }
  }, [runs, chain])

  async function handleSubmit() {
    const p = prompt.trim()
    if (!p || isDisabled) return
    setPrompt('')
    setSubmitting(true)
    try {
      await onSubmit(p)
    } catch {
      // onSubmit shows its own alert
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <div className="conversation-feed" ref={feedRef}>
        {chain.length === 0
          ? <div className="empty-hint">Start a new chat or select one from the sidebar.</div>
          : chain.map(runId => {
              const run = runs[runId]
              if (!run) return null
              const parentHistoryLen = run.parent_run_id
                ? (runs[run.parent_run_id]?.history?.length ?? 0)
                : 0
              return (
                <ConversationTurn
                  key={runId}
                  run={run}
                  parentHistoryLength={parentHistoryLen}
                  onApprove={onApprove}
                  onDeny={onDeny}
                />
              )
            })
        }
      </div>

      <div className="input-area">
        <div className={'input-wrapper' + (isDisabled ? ' disabled' : '')}>
          <input
            id="prompt-input"
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            placeholder={placeholder}
            disabled={isDisabled}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                handleSubmit()
              }
            }}
          />
          <button id="run-btn" onClick={handleSubmit} disabled={isDisabled}>&#9654;</button>
        </div>
      </div>
    </>
  )
}

function ConversationTurn({ run, parentHistoryLength, onApprove, onDeny }) {
  const [actionPending, setActionPending] = useState(false)
  const ownActions = (run.history || []).slice(parentHistoryLength)
  const showThinking = run.status === 'running' && (!run.history || run.history.length === 0)

  async function handleApprove() {
    setActionPending(true)
    try { await onApprove(run.run_id) } finally { setActionPending(false) }
  }

  async function handleDeny() {
    setActionPending(true)
    try { await onDeny(run.run_id) } finally { setActionPending(false) }
  }

  return (
    <>
      <div className="turn-user">
        <div className="user-bubble">{run.prompt}</div>
      </div>

      <div className="turn-agent">
        <div className="agent-label">Agent</div>

        {ownActions.map((action, i) => (
          <CommandCard key={i} action={action} />
        ))}

        {showThinking && (
          <div className="thinking">
            <span className="spin" /> Thinking\u2026
          </div>
        )}

        {run.status === 'waiting_approval' && run.pending_command && (
          <ApprovalCard
            run={run}
            onApprove={handleApprove}
            onDeny={handleDeny}
            pending={actionPending}
          />
        )}

        {run.status === 'done' && (
          <div className="summary-block">{run.final_message || '(no response)'}</div>
        )}

        {['failed', 'policy_denied'].includes(run.status) && run.final_message && (
          <div className={'status-banner s-' + run.status}>{run.final_message}</div>
        )}
      </div>
    </>
  )
}
