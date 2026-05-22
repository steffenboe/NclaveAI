import { useState, useEffect, useRef, useCallback } from 'react'
import Sidebar from './components/Sidebar'
import ConversationFeed from './components/ConversationFeed'
import ConvSkillsBar from './components/ConvSkillsBar'
import SkillsModal from './components/SkillsModal'

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

export default function App() {
  const [runs, setRuns] = useState({})
  const [runOrder, setRunOrder] = useState([])
  const [selectedRootId, setSelectedRootId] = useState(null)
  const [skillsModalOpen, setSkillsModalOpen] = useState(false)
  const [convSkillsData, setConvSkillsData] = useState([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])

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

  // Initial load — runs once on mount
  useEffect(() => {
    async function loadAll() {
      try {
        const res = await fetch('/api/agent/runs')
        if (!res.ok) return
        const list = await res.json()
        const newRuns = {}
        const newOrder = []
        for (const r of list) {
          newRuns[r.run_id] = r
          newOrder.push(r.run_id)
          if (r.status === 'running' || r.status === 'waiting_approval') {
            pollRun(r.run_id)
          }
        }
        setRuns(newRuns)
        setRunOrder(newOrder)
        const roots = newOrder.filter(id => !newRuns[id]?.parent_run_id)
        if (roots.length > 0) {
          const rootId = roots[roots.length - 1]
          setSelectedRootId(rootId)
          let current = rootId
          let tailId = rootId
          while (current) {
            tailId = current
            const next = newOrder.find(id => newRuns[id]?.parent_run_id === current) ?? null
            current = next
          }
          loadConvSkills(tailId)
        } else {
          loadConvSkillsForNewChat()
        }
      } catch {}
    }
    loadAll()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

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
      />
      <div className="main">
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
    </>
  )
}
