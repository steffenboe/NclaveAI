import { useState, useEffect } from 'react'
import { apiFetch as fetch } from '../apiFetch'

const TABS = ['Members', 'Skills', 'Skill Repo', 'LLM']

function TeamDetail({ team, allUsers, allSkills, onUpdated, onClose }) {
  const [tab, setTab] = useState('Members')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const memberIds = new Set(team.user_ids)
  const nonMembers = allUsers.filter(u => !memberIds.has(u.user_id))

  async function addMember(userId) {
    setSaving(true); setError('')
    try {
      const res = await fetch(`/api/teams/${team.team_id}/members/${userId}`, { method: 'POST' })
      if (!res.ok) { const d = await res.json().catch(() => ({})); setError(d.detail || 'Failed'); return }
      onUpdated(await res.json())
    } catch { setError('Network error') } finally { setSaving(false) }
  }

  async function removeMember(userId) {
    setSaving(true); setError('')
    try {
      const res = await fetch(`/api/teams/${team.team_id}/members/${userId}`, { method: 'DELETE' })
      if (!res.ok) { const d = await res.json().catch(() => ({})); setError(d.detail || 'Failed'); return }
      onUpdated(await res.json())
    } catch { setError('Network error') } finally { setSaving(false) }
  }

  const [pendingSkillIds, setPendingSkillIds] = useState(new Set(team.skill_ids))
  useEffect(() => { setPendingSkillIds(new Set(team.skill_ids)) }, [team.skill_ids])

  function toggleSkill(skillId) {
    setPendingSkillIds(prev => { const next = new Set(prev); next.has(skillId) ? next.delete(skillId) : next.add(skillId); return next })
  }

  async function saveSkills() {
    setSaving(true); setError('')
    try {
      const res = await fetch(`/api/teams/${team.team_id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skill_ids: [...pendingSkillIds] }) })
      if (!res.ok) { const d = await res.json().catch(() => ({})); setError(d.detail || 'Failed'); return }
      onUpdated(await res.json())
    } catch { setError('Network error') } finally { setSaving(false) }
  }

  const [repoUrl, setRepoUrl] = useState(team.skill_repo_url || '')
  const [repoBranch, setRepoBranch] = useState(team.skill_repo_branch || 'main')
  useEffect(() => { setRepoUrl(team.skill_repo_url || ''); setRepoBranch(team.skill_repo_branch || 'main') }, [team.skill_repo_url, team.skill_repo_branch])

  async function saveRepo() {
    setSaving(true); setError('')
    try {
      const res = await fetch(`/api/teams/${team.team_id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skill_repo_url: repoUrl || null, skill_repo_branch: repoBranch }) })
      if (!res.ok) { const d = await res.json().catch(() => ({})); setError(d.detail || 'Failed'); return }
      onUpdated(await res.json())
    } catch { setError('Network error') } finally { setSaving(false) }
  }

  const [llmUrl, setLlmUrl] = useState(team.llm_base_url || '')
  const [llmKey, setLlmKey] = useState('')
  useEffect(() => { setLlmUrl(team.llm_base_url || ''); setLlmKey('') }, [team.llm_base_url])

  async function saveLlm() {
    setSaving(true); setError('')
    const body = { llm_base_url: llmUrl || null }
    if (llmKey) body.llm_api_key = llmKey
    try {
      const res = await fetch(`/api/teams/${team.team_id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      if (!res.ok) { const d = await res.json().catch(() => ({})); setError(d.detail || 'Failed'); return }
      onUpdated(await res.json())
    } catch { setError('Network error') } finally { setSaving(false) }
  }

  const members = allUsers.filter(u => memberIds.has(u.user_id))

  return (
    <div className="team-detail">
      <div className="team-detail-header">
        <strong>{team.name}</strong>
        <button className="btn-close-sm" onClick={onClose} title="Back to list">✕</button>
      </div>

      <div className="team-tabs">
        {TABS.map(t => (
          <button key={t} className={'team-tab' + (tab === t ? ' active' : '')} onClick={() => { setTab(t); setError('') }}>{t}</button>
        ))}
      </div>

      {error && <p className="modal-error">{error}</p>}

      {tab === 'Members' && (
        <div className="team-tab-body">
          <h4>Current members</h4>
          {members.length === 0
            ? <p className="team-empty">No members yet.</p>
            : (
              <ul className="team-member-list">
                {members.map(u => (
                  <li key={u.user_id} className="team-member-row">
                    <span>{u.username}</span>
                    <span className={`role-badge role-${u.role}`}>{u.role}</span>
                    <button className="btn-delete-user" disabled={saving} onClick={() => removeMember(u.user_id)} title="Remove from team">🗑</button>
                  </li>
                ))}
              </ul>
            )
          }
          {nonMembers.length > 0 && (
            <>
              <h4 style={{ marginTop: '12px' }}>Add member</h4>
              <ul className="team-member-list">
                {nonMembers.map(u => (
                  <li key={u.user_id} className="team-member-row">
                    <span>{u.username}</span>
                    <span className={`role-badge role-${u.role}`}>{u.role}</span>
                    <button className="btn-secondary btn-sm" disabled={saving} onClick={() => addMember(u.user_id)}>+ Add</button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {tab === 'Skills' && (
        <div className="team-tab-body">
          <p className="team-hint">Select which skills are available to this team.</p>
          {allSkills.filter(s => s.source !== 'remote').length === 0
            ? <p className="team-empty">No local skills configured.</p>
            : (
              <ul className="team-skill-list">
                {allSkills.filter(s => s.source !== 'remote').map(s => (
                  <li key={s.id} className="team-skill-row">
                    <label>
                      <input type="checkbox" checked={pendingSkillIds.has(s.id)} onChange={() => toggleSkill(s.id)} />
                      <span className="team-skill-name">{s.name}</span>
                      <span className="team-skill-desc">{s.description}</span>
                    </label>
                  </li>
                ))}
              </ul>
            )
          }
          <button className="btn-primary" disabled={saving} onClick={saveSkills} style={{ marginTop: '10px' }}>
            {saving ? 'Saving…' : 'Save skills'}
          </button>
        </div>
      )}

      {tab === 'Skill Repo' && (
        <div className="team-tab-body">
          <p className="team-hint">Provide a Git repository URL to load remote skills for this team.</p>
          <label className="settings-label">Repository URL</label>
          <input className="settings-input" type="text" placeholder="https://github.com/org/skills-repo" value={repoUrl} onChange={e => setRepoUrl(e.target.value)} />
          <label className="settings-label" style={{ marginTop: '8px' }}>Branch</label>
          <input className="settings-input" type="text" placeholder="main" value={repoBranch} onChange={e => setRepoBranch(e.target.value)} />
          <button className="btn-primary" disabled={saving} onClick={saveRepo} style={{ marginTop: '10px' }}>
            {saving ? 'Saving…' : 'Save repo'}
          </button>
        </div>
      )}

      {tab === 'LLM' && (
        <div className="team-tab-body">
          <p className="team-hint">Override the global LLM endpoint for this team. Leave blank to use the global setting.</p>
          <label className="settings-label">LLM Base URL</label>
          <input className="settings-input" type="text" placeholder="https://api.openai.com" value={llmUrl} onChange={e => setLlmUrl(e.target.value)} />
          <label className="settings-label" style={{ marginTop: '8px' }}>API Key</label>
          <input className="settings-input" type="password" placeholder={team.has_llm_api_key ? '(key stored — enter new to replace)' : 'sk-…'} value={llmKey} onChange={e => setLlmKey(e.target.value)} />
          <button className="btn-primary" disabled={saving} onClick={saveLlm} style={{ marginTop: '10px' }}>
            {saving ? 'Saving…' : 'Save LLM settings'}
          </button>
        </div>
      )}
    </div>
  )
}

export default function TeamsModal({ onClose }) {
  const [teams, setTeams] = useState([])
  const [allUsers, setAllUsers] = useState([])
  const [allSkills, setAllSkills] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedTeamId, setSelectedTeamId] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')

  useEffect(() => { loadAll() }, [])

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') { if (selectedTeamId) setSelectedTeamId(null); else onClose() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose, selectedTeamId])

  async function loadAll() {
    setLoading(true); setError('')
    try {
      const [teamsRes, usersRes, skillsRes] = await Promise.all([fetch('/api/teams'), fetch('/api/users'), fetch('/api/skills')])
      if (!teamsRes.ok || !usersRes.ok) { setError('Failed to load data'); return }
      setTeams(await teamsRes.json())
      setAllUsers(await usersRes.json())
      setAllSkills(skillsRes.ok ? await skillsRes.json() : [])
    } catch { setError('Failed to load data') } finally { setLoading(false) }
  }

  function handleTeamUpdated(updatedTeam) {
    setTeams(prev => prev.map(t => t.team_id === updatedTeam.team_id ? updatedTeam : t))
  }

  async function handleCreate(e) {
    e.preventDefault(); setCreateError(''); setCreating(true)
    try {
      const res = await fetch('/api/teams', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: newName }) })
      if (!res.ok) { const d = await res.json().catch(() => ({})); setCreateError(d.detail || 'Failed to create team'); return }
      const created = await res.json()
      setTeams(prev => [...prev, created]); setNewName(''); setShowCreate(false); setSelectedTeamId(created.team_id)
    } catch { setCreateError('Network error') } finally { setCreating(false) }
  }

  async function handleDelete(teamId) {
    if (!confirm('Delete this team? Members will not be affected.')) return
    try {
      const res = await fetch(`/api/teams/${teamId}`, { method: 'DELETE' })
      if (!res.ok) { const d = await res.json().catch(() => ({})); alert(d.detail || 'Failed to delete'); return }
      setTeams(prev => prev.filter(t => t.team_id !== teamId))
      if (selectedTeamId === teamId) setSelectedTeamId(null)
    } catch { alert('Network error') }
  }

  const selectedTeam = teams.find(t => t.team_id === selectedTeamId)

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header">
          <h2>Team Management</h2>
          <button className="btn-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="modal-body teams-layout">
          <div className="teams-list-panel">
            {error && <p className="modal-error">{error}</p>}
            <div className="users-toolbar">
              <button className="btn-secondary" onClick={() => { setShowCreate(v => !v); setCreateError('') }}>
                {showCreate ? 'Cancel' : '+ New team'}
              </button>
            </div>
            {showCreate && (
              <form onSubmit={handleCreate} className="users-create-form">
                <input type="text" placeholder="Team name" value={newName} onChange={e => setNewName(e.target.value)} required autoFocus />
                {createError && <span className="users-create-error">{createError}</span>}
                <button type="submit" className="btn-primary" disabled={creating}>{creating ? 'Creating…' : 'Create'}</button>
              </form>
            )}
            {loading
              ? <p className="users-loading">Loading…</p>
              : teams.length === 0
                ? <p className="team-empty">No teams yet.</p>
                : (
                  <ul className="teams-list">
                    {teams.map(t => (
                      <li key={t.team_id} className={'teams-list-item' + (t.team_id === selectedTeamId ? ' active' : '')} onClick={() => setSelectedTeamId(t.team_id === selectedTeamId ? null : t.team_id)}>
                        <div className="teams-list-item-main">
                          <span className="teams-list-name">{t.name}</span>
                          <span className="teams-list-meta">{t.user_ids.length} member{t.user_ids.length !== 1 ? 's' : ''} · {t.skill_ids.length} skill{t.skill_ids.length !== 1 ? 's' : ''}</span>
                        </div>
                        <button className="btn-delete-user" title="Delete team" onClick={e => { e.stopPropagation(); handleDelete(t.team_id) }}>🗑</button>
                      </li>
                    ))}
                  </ul>
                )
            }
          </div>

          <div className="teams-detail-panel">
            {selectedTeam
              ? <TeamDetail team={selectedTeam} allUsers={allUsers} allSkills={allSkills} onUpdated={handleTeamUpdated} onClose={() => setSelectedTeamId(null)} />
              : <div className="teams-detail-placeholder"><span>← Select a team to configure it</span></div>
            }
          </div>
        </div>
      </div>
    </div>
  )
}
