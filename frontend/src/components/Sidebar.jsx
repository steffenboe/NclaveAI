import { getRoots, getChainTailStatus } from '../App'

function StatusBadge({ status }) {
  switch (status) {
    case 'running': return <span className="s-running"><span className="spin" /> running</span>
    case 'waiting_approval': return <span className="s-waiting_approval">● approval</span>
    case 'done': return <span className="s-done">✓ done</span>
    case 'failed': return <span className="s-failed">✗ failed</span>
    case 'policy_denied': return <span className="s-policy_denied">✗ denied</span>
    default: return <span>{status}</span>
  }
}

export default function Sidebar({
  runs, runOrder, selectedRootId,
  onNewChat, onSelectConversation, onDeleteConversation, onOpenSettings,
  searchQuery, searchResults, onSearch,
  user, onLogout, onOpenScheduledTasksModal, onOpenUsersModal, onOpenTeamsModal,
}) {
  const roots = getRoots(runs, runOrder).slice().reverse()
  const isSearching = searchQuery.trim().length > 0

  return (
    <div className="sidebar">
      <div className="sidebar-top">
        <button className="btn-new-chat" onClick={onNewChat}>+ New chat</button>
        <div className="sidebar-search-wrap">
          <input className="sidebar-search" type="search" placeholder="Search chats…" value={searchQuery} onChange={e => onSearch(e.target.value)} />
        </div>
      </div>

      <div className="sidebar-list">
        {isSearching
          ? (searchResults.length === 0
            ? <div style={{ color: '#484f58', fontSize: '11px', fontStyle: 'italic', padding: '8px 4px' }}>No results.</div>
            : searchResults.map(hit => (
                <div key={hit.run_id} className={'sidebar-item' + (hit.root_run_id === selectedRootId ? ' active' : '')} onClick={() => onSelectConversation(hit.root_run_id)}>
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
            ? <div style={{ color: '#484f58', fontSize: '11px', fontStyle: 'italic', padding: '8px 4px' }}>No conversations yet.</div>
            : roots.map(rootId => {
                const run = runs[rootId]
                const status = getChainTailStatus(runs, runOrder, rootId)
                return (
                  <div key={rootId} className={'sidebar-item' + (rootId === selectedRootId ? ' active' : '')} onClick={() => onSelectConversation(rootId)}>
                    <div className="sidebar-main">
                      <div className="sidebar-prompt">{run.prompt}</div>
                      <div className="sidebar-status"><StatusBadge status={status} /></div>
                    </div>
                    <button className="sidebar-delete" title="Delete conversation" aria-label="Delete conversation" onClick={e => { e.stopPropagation(); onDeleteConversation(rootId) }}>🗑</button>
                  </div>
                )
              })
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
              <button className="btn-users" onClick={onOpenTeamsModal} title="Manage teams">🏷</button>
            )}
            <button className="btn-logout" onClick={onLogout} title="Sign out">⏏</button>
          </div>
        )}
        <button className="btn-gear" onClick={onOpenSettings} title="Skills &amp; Settings">⚙</button>
      </div>
    </div>
  )
}
