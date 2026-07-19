import { useState, useEffect, useRef, useCallback } from 'react'
import Sidebar from './components/Sidebar'
import ConversationFeed from './components/ConversationFeed'
import ConvSkillsBar from './components/ConvSkillsBar'
import SkillsModal from './components/SkillsModal'
import ScheduledTasksModal from './components/ScheduledTasksModal'
import UsersModal from './components/UsersModal'
import TeamsModal from './components/TeamsModal'
import PolicyTestModal from './components/PolicyTestModal'
import LiveMode from './components/LiveMode'
import { useAuth } from './AuthContext'
import Login from './Login'
// Shadows the global fetch in this module so all API calls get 401 interception
import { apiFetch as fetch } from './apiFetch'

// ── Pure helpers ───────────────────────────────────────

export function getRoots(runs, runOrder) {
  return runOrder.filter(id => !runs[id]?.parent_run_id)
}

export function getChain(runs, runOrder, rootId) {
  const chain = []
  let current = rootId
  while (current) {
    chain.push(current)
    const next = runOrder.find(id => runs[id]?.parent_run_id === current) ?? null
    current = next
  }
  return chain
}

export function getChainTailStatus(runs, runOrder, rootId) {
  const chain = getChain(runs, runOrder, rootId)
  return runs[chain[chain.length - 1]]?.status ?? 'running'
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

// ── App ────────────────────────────────────────────────

// All authenticated UI lives here so hooks are never called conditionally.
function MainApp({ user, logout }) {
  const [runs, setRuns] = useState({})
  const [runOrder, setRunOrder] = useState([])
  const [selectedRootId, setSelectedRootId] = useState(null)
  const [skillsModalOpen, setSkillsModalOpen] = useState(false)
  const [scheduledTasksModalOpen, setScheduledTasksModalOpen] = useState(false)
  const [usersModalOpen, setUsersModalOpen] = useState(false)
  const [teamsModalOpen, setTeamsModalOpen] = useState(false)
  const [policyTestModalOpen, setPolicyTestModalOpen] = useState(false)
  const [convSkillsData, setConvSkillsData] = useState([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [liveModeEnabled, setLiveModeEnabled] = useState(false)
  const [currentResponse, setCurrentResponse] = useState('')

  const pendingOverridesRef = useRef({})
  const pollingRef = useRef(new Set())

  // Keep mutable refs in sync for use inside async polling callbacks
  const runsRef = useRef(runs)
  const runOrderRef = useRef(runOrder)
  useEffect(() => { runsRef.current = runs }, [runs])
  useEffect(() => { runOrderRef.current = runOrder }, [runOrder])

  // Debounced keyword search across all runs
  useEffect(() => {
    if (!searchQuery.trim()) {
      setSearchResults([])
      return
    }
    const tid = setTimeout(async () => {
      try {
        const res = await fetch('/api/agent/runs/search?q=' + encodeURIComponent(searchQuery))
        if (res.ok) setSearchResults(await res.json())
      } catch {}
    }, 300)
    return () => clearTimeout(tid)
  }, [searchQuery])

  const upsertRun = useCallback((run) => {
    setRuns(prev => ({ ...prev, [run.run_id]: run }))
    setRunOrder(prev => prev.includes(run.run_id) ? prev : [...prev, run.run_id])
  }, [])

  const pollRun = useCallback(async (runId) => {
    if (pollingRef.current.has(runId)) return
    pollingRef.current.add(runId)
    while (true) {
      try {
        const res = await fetch('/api/agent/runs/' + runId)
        if (!res.ok) break
        const data = await res.json()
        upsertRun(data)
        if (data.status !== 'running' && data.status !== 'waiting_approval') break
      } catch { break }
      await sleep(2000)
    }
    pollingRef.current.delete(runId)
  }, [upsertRun])

  const loadConvSkills = useCallback(async (runId) => {
    if (!runId) { setConvSkillsData([]); return }
    try {
      const res = await fetch(`/api/agent/runs/${runId}/skills`)
      if (res.ok) {
        setConvSkillsData(await res.json())
      } else {
        const fallback = await fetch('/api/skills')
        setConvSkillsData(fallback.ok
          ? (await fallback.json()).map(s => ({ ...s, effective_enabled: s.enabled }))
          : [])
      }
    } catch { setConvSkillsData([]) }
  }, [])

  const loadConvSkillsForNewChat = useCallback(async () => {
    pendingOverridesRef.current = {}
    try {
      const res = await fetch('/api/skills')
      setConvSkillsData(res.ok
        ? (await res.json()).map(s => ({ ...s, effective_enabled: s.enabled }))
        : [])
    } catch { setConvSkillsData([]) }
  }, [])

  const syncRuns = useCallback(async (initialize = false) => {
    try {
      const res = await fetch('/api/agent/runs')
      if (!res.ok) return
      const list = await res.json()

      setRuns(prev => {
        const next = { ...prev }
        for (const run of list) next[run.run_id] = run
        return next
      })
      setRunOrder(prev => {
        const ids = new Set(prev)
        const next = [...prev]
        for (const run of list) {
          if (!ids.has(run.run_id)) {
            next.push(run.run_id)
            ids.add(run.run_id)
          }
        }
        return next
      })

      for (const run of list) {
        if (run.status === 'running' || run.status === 'waiting_approval') {
          pollRun(run.run_id)
        }
      }

      if (initialize) {
        const initialRuns = {}
        const initialOrder = []
        for (const run of list) {
          initialRuns[run.run_id] = run
          initialOrder.push(run.run_id)
        }
        const roots = initialOrder.filter(id => !initialRuns[id]?.parent_run_id)
        if (roots.length > 0) {
          const rootId = roots[roots.length - 1]
          setSelectedRootId(rootId)
          let current = rootId
          let tailId = rootId
          while (current) {
            tailId = current
            const next = initialOrder.find(id => initialRuns[id]?.parent_run_id === current) ?? null
            current = next
          }
          loadConvSkills(tailId)
        } else {
          loadConvSkillsForNewChat()
        }
      }
    } catch {}
  }, [pollRun, loadConvSkills, loadConvSkillsForNewChat])

  // Initial load + periodic sync so scheduler-created runs appear without refresh
  useEffect(() => {
    syncRuns(true)
    const intervalId = setInterval(() => { syncRuns(false) }, 3000)
    return () => clearInterval(intervalId)
  }, [syncRuns])

  const newChat = useCallback(() => {
    setSelectedRootId(null)
    loadConvSkillsForNewChat()
  }, [loadConvSkillsForNewChat])

  const selectConversation = useCallback((rootId) => {
    setSelectedRootId(rootId)
    const chain = getChain(runsRef.current, runOrderRef.current, rootId)
    const tailId = chain[chain.length - 1]
    loadConvSkills(tailId)
  }, [loadConvSkills])

  const deleteConversation = useCallback(async (rootId) => {
    try {
      const res = await fetch('/api/agent/runs/' + rootId, { method: 'DELETE' })
      if (!res.ok) throw new Error('HTTP ' + res.status)

      const chain = getChain(runsRef.current, runOrderRef.current, rootId)
      const removeIds = new Set(chain)
      setRuns(prev => {
        const next = { ...prev }
        for (const rid of removeIds) delete next[rid]
        return next
      })
      setRunOrder(prev => prev.filter(id => !removeIds.has(id)))
      setSelectedRootId(prev => {
        if (prev === rootId) {
          loadConvSkillsForNewChat()
          return null
        }
        return prev
      })
    } catch (e) {
      alert('Failed to delete conversation: ' + e.message)
    }
  }, [loadConvSkillsForNewChat])

  const applyPendingOverrides = useCallback(async (runId) => {
    const entries = Object.entries(pendingOverridesRef.current)
    if (entries.length === 0) return
    await Promise.all(entries.map(([skillId, enabled]) =>
      fetch(`/api/agent/runs/${runId}/skills/${skillId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
    ))
    pendingOverridesRef.current = {}
  }, [])

  const submitRun = useCallback(async (prompt) => {
    const chain = selectedRootId
      ? getChain(runsRef.current, runOrderRef.current, selectedRootId)
      : []
    const contextRunId = chain.length > 0 ? chain[chain.length - 1] : null
    const inheritedHistoryLength = contextRunId
      ? (runsRef.current[contextRunId]?.history?.length ?? 0)
      : 0

    const body = { prompt }
    if (contextRunId) body.context_run_id = contextRunId

    const res = await fetch('/api/agent/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error('HTTP ' + res.status)
    const data = await res.json()

    upsertRun({
      run_id: data.run_id,
      prompt,
      status: 'running',
      history: [],
      history_start_index: inheritedHistoryLength,
      parent_run_id: contextRunId,
    })
    if (!contextRunId) setSelectedRootId(data.run_id)

    await applyPendingOverrides(data.run_id)
    pollRun(data.run_id)
    loadConvSkills(data.run_id)
    
    // For live mode, we'll capture the response when the run completes
    // This is handled by the pollRun function which updates the run state
  }, [selectedRootId, upsertRun, applyPendingOverrides, pollRun, loadConvSkills])

  const toggleConvSkill = useCallback(async (tailRunId, skill) => {
    if (skill.source === 'remote') return
    if (!tailRunId) {
      const next = !skill.effective_enabled
      pendingOverridesRef.current[skill.id] = next
      setConvSkillsData(prev => prev.map(s => s.id === skill.id ? { ...s, effective_enabled: next } : s))
      return
    }
    try {
      const res = await fetch(`/api/agent/runs/${tailRunId}/skills/${skill.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !skill.effective_enabled }),
      })
      if (!res.ok) return
      await loadConvSkills(tailRunId)
    } catch {}
  }, [loadConvSkills])

  const handleApprove = useCallback(async (runId) => {
    await fetch('/api/agent/runs/' + runId + '/approve', { method: 'POST' })
    const r = await fetch('/api/agent/runs/' + runId)
    if (r.ok) upsertRun(await r.json())
    pollRun(runId)
  }, [upsertRun, pollRun])

  const handleDeny = useCallback(async (runId) => {
    await fetch('/api/agent/runs/' + runId + '/deny', { method: 'POST' })
    const r = await fetch('/api/agent/runs/' + runId)
    if (r.ok) upsertRun(await r.json())
  }, [upsertRun])

  const handleScheduledRunCreated = useCallback((run) => {
    if (!run?.run_id) return
    upsertRun({
      run_id: run.run_id,
      prompt: run.prompt || 'Scheduled run',
      status: run.status || 'running',
      history: [],
      history_start_index: 0,
      parent_run_id: null,
    })
    setSelectedRootId(run.run_id)
    pollRun(run.run_id)
  }, [upsertRun, pollRun])

  const chain = selectedRootId ? getChain(runs, runOrder, selectedRootId) : []
  const tailRunId = chain.length > 0 ? chain[chain.length - 1] : null
  const tailRun = tailRunId ? runs[tailRunId] : null

  return (
    <>
      <Sidebar
        runs={runs}
        runOrder={runOrder}
        selectedRootId={selectedRootId}
        onNewChat={newChat}
        onSelectConversation={selectConversation}
        onDeleteConversation={deleteConversation}
        onOpenSettings={() => setSkillsModalOpen(true)}
        searchQuery={searchQuery}
        searchResults={searchResults}
        onSearch={setSearchQuery}
        user={user}
        onLogout={logout}
        onOpenScheduledTasksModal={() => setScheduledTasksModalOpen(true)}
        onOpenUsersModal={user?.role === 'admin' ? () => setUsersModalOpen(true) : null}
        onOpenTeamsModal={user?.role === 'admin' ? () => setTeamsModalOpen(true) : null}
        onOpenPolicyTest={user?.role === 'admin' ? () => setPolicyTestModalOpen(true) : null}
      />
      <div className="main">
        {liveModeEnabled ? (
          <LiveMode
            onSubmit={submitRun}
            isProcessing={tailRun?.status === 'running'}
            currentResponse={currentResponse}
          />
        ) : (
          <>
            <ConversationFeed
              runs={runs}
              chain={chain}
              tailRun={tailRun}
              onApprove={handleApprove}
              onDeny={handleDeny}
              onSubmit={submitRun}
            />
            <ConvSkillsBar
              tailRunId={tailRunId}
              convSkillsData={convSkillsData}
              onToggleSkill={toggleConvSkill}
            />
          </>
        )}
        <button
          className="live-mode-toggle"
          onClick={() => setLiveModeEnabled(!liveModeEnabled)}
          title={liveModeEnabled ? 'Switch to text mode' : 'Switch to voice mode'}
        >
          {liveModeEnabled ? '💬' : '🎤'}
        </button>
      </div>
      {skillsModalOpen && (
        <SkillsModal onClose={() => {
          setSkillsModalOpen(false)

          if (tailRunId) {
            loadConvSkills(tailRunId)
          } else {
            loadConvSkillsForNewChat()
          }
        }} />
      )}
      {scheduledTasksModalOpen && (
        <ScheduledTasksModal
          onRunCreated={handleScheduledRunCreated}
          onClose={() => setScheduledTasksModalOpen(false)}
        />
      )}
      {user?.role === 'admin' && usersModalOpen && (
        <UsersModal onClose={() => setUsersModalOpen(false)} />
      )}
      {user?.role === 'admin' && teamsModalOpen && (
        <TeamsModal onClose={() => setTeamsModalOpen(false)} />
      )}
      {user?.role === 'admin' && policyTestModalOpen && (
        <PolicyTestModal onClose={() => setPolicyTestModalOpen(false)} />
      )}
      {user?.role === 'admin' && (
        <LiveMode />
      )}
      {liveModeEnabled && (
        <div className="live-mode-indicator">
          Live Mode is enabled
        </div>
      )}
      {!liveModeEnabled && (
        <button className="toggle-live-mode" onClick={() => setLiveModeEnabled(true)}>
          Enable Live Mode
        </button>
      )}
    </>
  )
}

// Auth gate — renders loading or Login before the full app mounts.
export default function App() {
  const { user, logout } = useAuth()
  if (user === undefined) return <div className="auth-loading">Authenticating…</div>
  if (user === null) return <Login />
  return <MainApp user={user} logout={logout} />
}
