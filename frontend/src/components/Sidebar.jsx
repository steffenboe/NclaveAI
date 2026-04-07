import { getRoots, getChain, getChainTailStatus } from '../App'

function StatusBadge({ status }) {
  switch (status) {
    case 'running':
      return <span className="s-running"><span className="spin" /> running</span>
    case 'waiting_approval':
      return <span className="s-waiting_approval">● approval</span>
    case 'done':
      return <span className="s-done">✓ done</span>
    case 'failed':
      return <span className="s-failed">✗ failed</span>
    case 'policy_denied':
      return <span className="s-policy_denied">✗ denied</span>
    default:
      return <span>{status}</span>
  }
}

export default function Sidebar({
  runs, runOrder, selectedRootId,
  onNewChat, onSelectConversation, onDeleteConversation, onOpenSettings,
}) {
  const roots = getRoots(runs, runOrder).slice().reverse()

  return (
    <div className="sidebar">
      <div className="sidebar-top">
        <button className="btn-new-chat" onClick={onNewChat}>+ New chat</button>
      </div>

      <div className="sidebar-list">
        {roots.length === 0
          ? <div style={{ color: '#484f58', fontSize: '11px', fontStyle: 'italic', padding: '8px 4px' }}>
              No conversations yet.
            </div>
          : roots.map(rootId => {
              const run = runs[rootId]
              const status = getChainTailStatus(runs, runOrder, rootId)
              return (
                <div
                  key={rootId}
                  className={'sidebar-item' + (rootId === selectedRootId ? ' active' : '')}
                  onClick={() => onSelectConversation(rootId)}
                >
                  <div className="sidebar-main">
                    <div className="sidebar-prompt">{run.prompt}</div>
                    <div className="sidebar-status">
                      <StatusBadge status={status} />
                    </div>
                  </div>
                  <button
                    className="sidebar-delete"
                    title="Delete conversation"
                    aria-label="Delete conversation"
                    onClick={e => { e.stopPropagation(); onDeleteConversation(rootId) }}
                  >
                    🗑
                  </button>
                </div>
              )
            })
        }
      </div>

      <div className="sidebar-bottom">
        <button className="btn-gear" onClick={onOpenSettings} title="Skills &amp; Settings">⚙</button>
      </div>
    </div>
  )
}
