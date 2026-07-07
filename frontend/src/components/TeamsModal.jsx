import { useState, useEffect } from 'react'

export default function TeamsModal({ onClose }) {
  const [teams, setTeams] = useState([])
  const [users, setUsers] = useState([])
  const [skills, setSkills] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedTeam, setSelectedTeam] = useState(null)

  // Create form
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')

  // Edit state
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [editSkillIds, setEditSkillIds] = useState([])
  const [editRepoUrl, setEditRepoUrl] = useState('')
  const [editRepoBranch, setEditRepoBranch] = useState('main')
  const [editLlmUrl, setEditLlmUrl] = useState('')
  const [editLlmKey, setEditLlmKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  useEffect(() => { loadAll() }, [])

  useEffect(() => {
    function onKeyDown(e) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  async function loadAll() {
    setLoading(true)
    setError('')
    try {
      const [tr, ur, sr] = await Promise.all([
        fetch('/api/teams'), fetch('/api/users'), fetch('/api/skills'),
      ])
      if (!tr.ok || !ur.ok || !sr.ok) { setError('Failed to load data'); return }
      const [ts, us, ss] = await Promise.all([tr.json(), ur.json(), sr.json()])
      setTeams(ts)
      setUsers(us)
      setSkills(ss)
      if (selectedTeam) {
        const refreshed = ts.find(t => t.team_id === selectedTeam.team_id)
        if (refreshed) setSelectedTeam(refreshed)
      }
    } catch {
      setError('Failed to load data')
    } finally {
      setLoading(false)
    }
  }

  function selectTeam(team) {
    setSelectedTeam(team)
    setEditing(false)
    setSaveError('')
  }

  function startEdit(team) {
    setEditName(team.name)
    setEditSkillIds([...team.skill_ids])
    setEditRepoUrl(team.skill_repo_url || '')
    setEditRepoBranch(team.skill_repo_branch || 'main')
    setEditLlmUrl(team.llm_base_url || '')
    setEditLlmKey('')
    setSaveError('')
    setEditing(true)
  }

  async function handleCreate(e) {
    e.preventDefault()
    setCreateError('')
    setCreating(true)
    try {
      const res = await fetch('/api/teams', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setCreateError(data.detail || 'Failed to create team')
        return
      }
      const created = await res.json()
      setNewName('')
      setShowCreate(false)
      await loadAll()
      setSelectedTeam(created)
    } catch {
      setCreateError('Failed to create team')
    } finally {
      setCreating(false)
    }
  }

  async function handleSave(e) {
    e.preventDefault()
    setSaveError('')
    setSaving(true)
    try {
      const body = {
        name: editName,
        skill_ids: editSkillIds,
        skill_repo_url: editRepoUrl || null,
        skill_repo_branch: editRepoBranch || 'main',
        llm_base_url: editLlmUrl || null,
      }
      if (editLlmKey) body.llm_api_key = editLlmKey
      const res = await fetch(`/api/teams/${selectedTeam.team_id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setSaveError(data.detail || 'Failed to save')
        return
      }
      const updated = await res.json()
      setEditing(false)
      await loadAll()
      setSelectedTeam(updated)
    } catch {
      setSaveError('Failed to save')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(team) {
    if (!confirm(`Delete team "${team.name}"?`)) return
    try {
      const res = await fetch(`/api/teams/${team.team_id}`, { method: 'DELETE' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        alert(data.detail || 'Failed to delete team')
        return
      }
      setSelectedTeam(null)
      setEditing(false)
      await loadAll()
    } catch {
      alert('Failed to delete team')
    }
  }

  async function handleAddMember(userId) {
    try {
      const res = await fetch(`/api/teams/${selectedTeam.team_id}/members/${userId}`, { method: 'POST' })
      if (!res.ok) { const d = await res.json().catch(() => ({})); alert(d.detail || 'Failed'); return }
      const updated = await res.json()
      setSelectedTeam(updated)
      setTeams(prev => prev.map(t => t.team_id === updated.team_id ? updated : t))
    } catch { alert('Failed to add member') }
  }

  async function handleRemoveMember(userId) {
    try {
      const res = await fetch(`/api/teams/${selectedTeam.team_id}/members/${userId}`, { method: 'DELETE' })
      if (!res.ok) { const d = await res.json().catch(() => ({})); alert(d.detail || 'Failed'); return }
      const updated = await res.json()
      setSelectedTeam(updated)
      setTeams(prev => prev.map(t => t.team_id === updated.team_id ? updated : t))
    } catch { alert('Failed to remove member') }
  }

  function toggleSkill(skillId) {
    setEditSkillIds(prev =>
      prev.includes(skillId) ? prev.filter(id => id !== skillId) : [...prev, skillId]
    )
  }

  const members = users.filter(u => selectedTeam?.user_ids.includes(u.user_id))
  const nonMembers = users.filter(u => !selectedTeam?.user_ids.includes(u.user_id))

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal teams-modal">

        <div className="modal-header">
          <h2>Teams</h2>
          <button className="btn-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {error && <p className="teams-error">{error}</p>}

        <div className="teams-layout">

          {/* ── Team list ── */}
          <div className="teams-list">
            {!showCreate ? (
              <button className="modal-add-btn" onClick={() => setShowCreate(true)}>
                + New team
              </button>
            ) : (
              <form onSubmit={handleCreate} className="teams-create-form">
                <input
                  className="teams-input"
                  placeholder="Team name"
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  autoFocus
                  required
                />
                {createError && <span className="teams-field-error">{createError}</span>}
                <div className="teams-form-actions">
                  <button className="btn-primary" type="submit" disabled={creating}>
                    {creating ? 'Creating…' : 'Create'}
                  </button>
                  <button
                    className="btn-secondary"
                    type="button"
                    onClick={() => { setShowCreate(false); setCreateError('') }}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}

            <div className="teams-list-items">
              {loading ? (
                <p className="no-items-msg">Loading…</p>
              ) : teams.length === 0 ? (
                <p className="no-items-msg">No teams yet.</p>
              ) : teams.map(team => (
                <div
                  key={team.team_id}
                  className={'teams-list-item' + (selectedTeam?.team_id === team.team_id ? ' active' : '')}
                  onClick={() => selectTeam(team)}
                >
                  <span className="teams-list-name">{team.name}</span>
                  <span className="teams-list-meta">
                    {team.user_ids.length} member{team.user_ids.length !== 1 ? 's' : ''}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* ── Detail / Edit panel ── */}
          <div className="teams-detail">
            {!selectedTeam ? (
              <p className="no-items-msg">Select a team to view or edit.</p>

            ) : editing ? (
              <form onSubmit={handleSave} className="teams-edit-form">

                <div className="settings-field">
                  <span className="settings-field-title">Name</span>
                  <input
                    className="teams-input"
                    value={editName}
                    onChange={e => setEditName(e.target.value)}
                    required
                  />
                </div>

                <div className="settings-field">
                  <span className="settings-field-title">
                    Assigned skills
                    {editSkillIds.length > 0 && (
                      <span className="teams-count"> · {editSkillIds.length} selected</span>
                    )}
                  </span>
                  <div className="teams-skill-picker">
                    {skills.length === 0
                      ? <span className="teams-empty-note">No skills configured yet.</span>
                      : skills.map(s => (
                          <label key={s.id} className="teams-skill-row">
                            <input
                              type="checkbox"
                              checked={editSkillIds.includes(s.id)}
                              onChange={() => toggleSkill(s.id)}
                            />
                            <span className="teams-skill-name">{s.name}</span>
                          </label>
                        ))
                    }
                  </div>
                </div>

                <div className="settings-field">
                  <span className="settings-field-title">Skill repository URL</span>
                  <input
                    className="teams-input"
                    placeholder="https://github.com/org/skills-repo"
                    value={editRepoUrl}
                    onChange={e => setEditRepoUrl(e.target.value)}
                  />
                </div>

                <div className="settings-field">
                  <span className="settings-field-title">Repository branch</span>
                  <input
                    className="teams-input"
                    placeholder="main"
                    value={editRepoBranch}
                    onChange={e => setEditRepoBranch(e.target.value)}
                  />
                </div>

                <div className="settings-field">
                  <span className="settings-field-title">LLM base URL</span>
                  <input
                    className="teams-input"
                    placeholder="https://api.openai.com"
                    value={editLlmUrl}
                    onChange={e => setEditLlmUrl(e.target.value)}
                  />
                </div>

                <div className="settings-field">
                  <span className="settings-field-title">
                    LLM API key
                    {selectedTeam.has_llm_api_key && <span className="teams-key-set"> · key set</span>}
                  </span>
                  <input
                    className="teams-input"
                    type="password"
                    placeholder={selectedTeam.has_llm_api_key ? '(unchanged)' : 'sk-…'}
                    value={editLlmKey}
                    onChange={e => setEditLlmKey(e.target.value)}
                    autoComplete="new-password"
                  />
                </div>

                {saveError && <span className="teams-field-error">{saveError}</span>}

                <div className="teams-form-actions">
                  <button className="btn-primary" type="submit" disabled={saving}>
                    {saving ? 'Saving…' : 'Save'}
                  </button>
                  <button className="btn-secondary" type="button" onClick={() => setEditing(false)}>
                    Cancel
                  </button>
                </div>
              </form>

            ) : (
              <>
                <div className="teams-detail-header">
                  <h3 className="teams-detail-title">{selectedTeam.name}</h3>
                  <div className="teams-detail-actions">
                    <button className="btn-secondary btn-sm" onClick={() => startEdit(selectedTeam)}>Edit</button>
                    <button className="btn-danger btn-sm" onClick={() => handleDelete(selectedTeam)}>Delete</button>
                  </div>
                </div>

                {/* Skills */}
                <div className="teams-section">
                  <span className="teams-section-label">Skills</span>
                  {selectedTeam.skill_ids.length === 0 ? (
                    <p className="teams-empty-note">No skills assigned.</p>
                  ) : (
                    <div className="teams-tag-list">
                      {selectedTeam.skill_ids.map(id => {
                        const skill = skills.find(s => s.id === id)
                        return (
                          <span key={id} className="teams-tag">{skill?.name ?? id}</span>
                        )
                      })}
                    </div>
                  )}
                </div>

                {/* Repo + LLM */}
                {(selectedTeam.skill_repo_url || selectedTeam.llm_base_url) && (
                  <div className="teams-section">
                    <span className="teams-section-label">Configuration</span>
                    <div className="teams-config-rows">
                      {selectedTeam.skill_repo_url && (
                        <div className="teams-config-row">
                          <span className="teams-config-key">Skill repo</span>
                          <span className="teams-config-val">
                            {selectedTeam.skill_repo_url}
                            {selectedTeam.skill_repo_branch !== 'main' && (
                              <span className="teams-config-branch"> ({selectedTeam.skill_repo_branch})</span>
                            )}
                          </span>
                        </div>
                      )}
                      {selectedTeam.llm_base_url && (
                        <div className="teams-config-row">
                          <span className="teams-config-key">LLM</span>
                          <span className="teams-config-val">
                            {selectedTeam.llm_base_url}
                            {selectedTeam.has_llm_api_key && (
                              <span className="teams-key-set"> · key set</span>
                            )}
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Members */}
                <div className="teams-section">
                  <span className="teams-section-label">Members</span>
                  {members.length === 0 ? (
                    <p className="teams-empty-note">No members assigned.</p>
                  ) : (
                    <table className="users-table teams-members-table">
                      <tbody>
                        {members.map(u => (
                          <tr key={u.user_id}>
                            <td>{u.username}</td>
                            <td><span className={`role-badge role-${u.role}`}>{u.role}</span></td>
                            <td className="teams-members-action">
                              <button
                                className="btn-delete-user"
                                onClick={() => handleRemoveMember(u.user_id)}
                                title="Remove from team"
                              >
                                ✕
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {nonMembers.length > 0 && (
                    <div className="teams-add-members">
                      <span className="teams-section-sublabel">Add member</span>
                      <div className="teams-tag-list">
                        {nonMembers.map(u => (
                          <button
                            key={u.user_id}
                            className="teams-add-member-btn"
                            onClick={() => handleAddMember(u.user_id)}
                          >
                            + {u.username}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
