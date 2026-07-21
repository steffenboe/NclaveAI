import { getRoots, getChainTailStatus } from '../App'

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

// Helper function to determine date group
function getDateGroup(createdAt) {
  if (!createdAt) return 'Older'
  
  const now = new Date()
  const chatDate = new Date(createdAt)
  const diffTime = now - chatDate
  const diffDays = Math.floor(diffTime / (1000 * 60 * 60 * 24))
  
  // Check if it's today (same calendar day)
  if (now.toDateString() === chatDate.toDateString()) {
    return 'Today'
  }
  
  // Check if it's yesterday
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  if (yesterday.toDateString() === chatDate.toDateString()) {
    return 'Yesterday'
  }
  
  if (diffDays < 7) return 'This Week'
  if (diffDays < 30) return 'This Month'
  return 'Older'
}

// Group chats by date
function groupChatsByDate(chats, runs) {
  const groups = {}
  const groupOrder = ['Today', 'Yesterday', 'This Week', 'This Month', 'Older']
  
  chats.forEach(chat => {
    const run = runs[chat.runId]
    const createdAt = run?.created_at
    const group = getDateGroup(createdAt)
    
    if (!groups[group]) {
      groups[group] = []
    }
    groups[group].push(chat)
  })
  
  // Return groups in the specified order
  return groupOrder
    .filter(groupName => groups[groupName] && groups[groupName].length > 0)
    .map(groupName => ({ name: groupName, chats: groups[groupName] }))
}

export default function Sidebar({
  runs, runOrder, selectedRootId,
  onNewChat, onSelectConversation, onDeleteConversation, onOpenSettings,
  searchQuery, searchResults, onSearch,
  user, onLogout, onOpenScheduledTasksModal, onOpenUsersModal, onOpenTeamsModal, onOpenPolicyTest,
}) {
  const roots = getRoots(runs, runOrder).slice().reverse()
  const isSearching = searchQuery.trim().length > 0

  // Group roots by date for display
  const rootsWithRuns = roots.map(rootId => ({
    runId: rootId,
    run: runs[rootId]
  }))
  const groupedChats = !isSearching ? groupChatsByDate(rootsWithRuns, runs) : []

  return (
    <div className="sidebar">
      <div className="sidebar-top">
        <button className="btn-new-chat" onClick={onNewChat}>+ New chat</button>
        <div className="sidebar-search-wrap">
          <input
            className="sidebar-search"
            type="search"
            placeholder="Search chats…"
            value={searchQuery}
            onChange={e => onSearch(e.target.value)}
          />
        </div>
      </div>

      <div className="sidebar-list">
        {isSearching
          ? (searchResults.length === 0
            ? <div style={{ color: '#484f58', fontSize: '11px', fontStyle: 'italic', padding: '8px 4px' }}>
                No results.
              </div>
            : searchResults.map(hit => (
                <div
                  key={hit.run_id}
                  className={'sidebar-item' + (hit.root_run_id === selectedRootId ? ' active' : '')}
                  onClick={() => onSelectConversation(hit.root_run_id)}
                >
                  <div className="sidebar-main">
                    <div className="sidebar-prompt">{hit.prompt}</div>
                    <div className="sidebar-status sidebar-match-hint">
                      <span className="sidebar-match-in">{hit.matched_in} · </span>
                      <StatusBadge status={hit.status} />
                    </div>
                  </div>
                </div>
              ))
          )
          : (roots.length === 0
            ? <div style={{ color: '#484f58', fontSize: '11px', fontStyle: 'italic', padding: '8px 4px' }}>
                No conversations yet.
              </div>
            : groupedChats.map(group => (
                <div key={group.name} className="chat-group">
                  <div className="chat-group-header">{group.name}</div>
                  {group.chats.map(({ runId }) => {
                    const run = runs[runId]
                    const status = getChainTailStatus(runs, runOrder, runId)
                    return (
                      <div
                        key={runId}
                        className={'sidebar-item' + (runId === selectedRootId ? ' active' : '')}
                        onClick={() => onSelectConversation(runId)}
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
                          onClick={e => { e.stopPropagation(); onDeleteConversation(runId) }}
                        >
                          🗑
                        </button>
                      </div>
                    )
                  })}
                </div>
              ))
          )
        }
      </div>

      <div className="sidebar-bottom">
        {user && (
          <div className="sidebar-user">
            <span className="sidebar-username" title={user.username}>{user.username}</span>
            {onOpenScheduledTasksModal && (
              <button className="btn-users" onClick={onOpenScheduledTasksModal} title="Scheduled tasks">🗓</button>
            )}
            {onOpenUsersModal && (
              <button className="btn-users" onClick={onOpenUsersModal} title="Manage users">👥</button>
            )}
            {onOpenTeamsModal && (
              <button className="btn-users" onClick={onOpenTeamsModal} title="Manage teams">🏢</button>
            )}
            {onOpenPolicyTest && (
              <button className="btn-users" onClick={onOpenPolicyTest} title="Test OPA policies">🧪</button>
            )}
            <button className="btn-logout" onClick={onLogout} title="Sign out">⏏</button>
          </div>
        )}
        <button className="btn-gear" onClick={onOpenSettings} title="Skills &amp; Settings">⚙</button>
      </div>
    </div>
  )
}
