import { useState, useEffect, useRef } from 'react'
import { useAuth } from '../AuthContext'

const MASKED_TOKEN_VALUE = '********'

export default function SkillsModal({ onClose }) {
  const { user, setUser } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [approvalRequired, setApprovalRequired] = useState(user?.require_approval ?? false)
  const [globalApprovalRequired, setGlobalApprovalRequired] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [changingPassword, setChangingPassword] = useState(false)
  const [passwordError, setPasswordError] = useState('')
  const [passwordSuccess, setPasswordSuccess] = useState(false)
  const [llmEndpoint, setLlmEndpoint] = useState('')
  const [llmToken, setLlmToken] = useState('')
  const [tokenMasked, setTokenMasked] = useState(false)
  const [tokenHelp, setTokenHelp] = useState('')
  const [repoUrl, setRepoUrl] = useState('')
  const [repoBranch, setRepoBranch] = useState('main')
  const [savingRepo, setSavingRepo] = useState(false)
  const [repoSaveError, setRepoSaveError] = useState('')
  const [skills, setSkills] = useState([])
  const [skillsRepoConfigured, setSkillsRepoConfigured] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [defaultModel, setDefaultModel] = useState('')
  const [availableModels, setAvailableModels] = useState([])
  const [fetchingModels, setFetchingModels] = useState(false)
  const [fetchModelsError, setFetchModelsError] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [savingSystemPrompt, setSavingSystemPrompt] = useState(false)
  // skillForm: null = hidden, {} = new skill, { skill } = editing existing
  const [skillForm, setSkillForm] = useState(null)
  const [form, setForm] = useState({ name: '', description: '', policy: '' })
  const [policyPopupOpen, setPolicyPopupOpen] = useState(false)
  const [policyDesc, setPolicyDesc] = useState('')
  const [generating, setGenerating] = useState(false)
  // detailSkill: null = hidden, skill object = showing details
  const [detailSkill, setDetailSkill] = useState(null)
  const detailSkillRef = useRef(null)

  // API keys
  const [apiKeys, setApiKeys] = useState([])
  const [newKeyName, setNewKeyName] = useState('')
  const [creatingKey, setCreatingKey] = useState(false)
  const [newlyCreatedKey, setNewlyCreatedKey] = useState(null)
  const [keyCopied, setKeyCopied] = useState(false)

  useEffect(() => {
    detailSkillRef.current = detailSkill
  }, [detailSkill])

  useEffect(() => {
    if (isAdmin) {
      loadSettings()
      loadSecrets()
      fetchModelsFromApi()
    } else {
      fetch('/api/settings/approval')
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) setGlobalApprovalRequired(d.approval_required) })
        .catch(() => {})
    }
    loadSkills()
    loadApiKeys()
  }, [])

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') {
        if (detailSkillRef.current) setDetailSkill(null)
        else onClose()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  async function loadSettings() {
    try {
      const res = await fetch('/api/settings')
      if (!res.ok) return
      const data = await res.json()
      setApprovalRequired(data.approval_required)
      if (typeof data.llm_base_url === 'string') setLlmEndpoint(data.llm_base_url)
      if (typeof data.skills_repo_configured === 'boolean') setSkillsRepoConfigured(data.skills_repo_configured)
      if (typeof data.skills_repo_url === 'string') setRepoUrl(data.skills_repo_url)
      else setRepoUrl('')
      if (typeof data.skills_repo_branch === 'string') setRepoBranch(data.skills_repo_branch)
      if (data.has_llm_api_key) {
        setLlmToken(MASKED_TOKEN_VALUE)
        setTokenMasked(true)
        setTokenHelp('A token is currently configured and masked. Type a new one to replace it.')
      } else {
        setLlmToken('')
        setTokenMasked(false)
        setTokenHelp('No token configured yet.')
      }
      if (data.default_model) setDefaultModel(data.default_model)
      if (typeof data.system_prompt === 'string') setSystemPrompt(data.system_prompt)
      else setSystemPrompt('')
    } catch {}
  }

  async function loadSkills() {
    try {
      const res = await fetch('/api/skills')
      if (res.ok) setSkills(await res.json())
    } catch {}
  }

  async function loadApiKeys() {
    try {
      const res = await fetch('/api/auth/api-keys')
      if (res.ok) setApiKeys(await res.json())
    } catch {}
  }

  async function createApiKey(e) {
    e.preventDefault()
    if (!newKeyName.trim()) return
    setCreatingKey(true)
    try {
      const res = await fetch('/api/auth/api-keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newKeyName.trim() }),
      })
      if (!res.ok) { alert('Failed to create API key'); return }
      const data = await res.json()
      setNewlyCreatedKey(data)
      setKeyCopied(false)
      setNewKeyName('')
      await loadApiKeys()
    } catch {
      alert('Failed to create API key')
    } finally {
      setCreatingKey(false)
    }
  }

  async function revokeApiKey(keyId, keyName) {
    if (!confirm(`Revoke API key "${keyName}"? This cannot be undone.`)) return
    const res = await fetch(`/api/auth/api-keys/${keyId}`, { method: 'DELETE' })
    if (!res.ok) { alert('Failed to revoke API key'); return }
    if (newlyCreatedKey?.key_id === keyId) setNewlyCreatedKey(null)
    await loadApiKeys()
  }

  function copyKey() {
    if (!newlyCreatedKey) return
    navigator.clipboard.writeText(newlyCreatedKey.key).then(() => {
      setKeyCopied(true)
      setTimeout(() => setKeyCopied(false), 2000)
    }).catch(() => {})
  }

  async function changePassword(e) {
    e.preventDefault()
    setPasswordError('')
    setPasswordSuccess(false)
    setChangingPassword(true)
    try {
      const res = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setPasswordError(data.detail || 'Failed to change password')
        return
      }
      setCurrentPassword('')
      setNewPassword('')
      setPasswordSuccess(true)
    } catch {
      setPasswordError('Failed to change password')
    } finally {
      setChangingPassword(false)
    }
  }

  async function onApprovalToggle(checked) {
    if (isAdmin) {
      try {
        const res = await fetch('/api/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ approval_required: checked }),
        })
        if (res.ok) setApprovalRequired(checked)
      } catch {}
    } else {
      try {
        const res = await fetch(`/api/users/${user.user_id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ require_approval: checked }),
        })
        if (res.ok) {
          const updated = await res.json()
          setApprovalRequired(checked)
          setUser(updated)
        }
      } catch {}
    }
  }

  async function saveLlmSettings() {
    const endpoint = llmEndpoint.trim()
    if (!endpoint) { alert('LLM endpoint is required.'); return }
    const payload = { llm_base_url: endpoint }
    if (!tokenMasked && llmToken.trim()) payload.llm_api_key = llmToken.trim()
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'HTTP ' + res.status)
      }
      await loadSettings()
      await fetchModelsFromApi()
      alert('LLM settings saved.')
    } catch (e) { alert('Failed to save LLM settings: ' + e.message) }
  }

  async function saveRepoSettings() {
    setSavingRepo(true)
    setRepoSaveError('')
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skills_repo_url: repoUrl.trim() || null,
          skills_repo_branch: repoBranch.trim() || 'main',
        }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'HTTP ' + res.status)
      }
      await loadSettings()
      await loadSkills()
    } catch (e) {
      setRepoSaveError(e.message)
    } finally {
      setSavingRepo(false)
    }
  }

  async function fetchModelsFromApi() {
    setFetchingModels(true)
    setFetchModelsError('')
    try {
      const res = await fetch('/api/models')
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'HTTP ' + res.status)
      }
      const data = await res.json()
      setAvailableModels(data.available_models)
      setDefaultModel(prev => prev || data.default_model)
    } catch (e) {
      setFetchModelsError(e.message)
    } finally {
      setFetchingModels(false)
    }
  }

  async function saveSystemPrompt() {
    setSavingSystemPrompt(true)
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ system_prompt: systemPrompt.trim() || null }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'HTTP ' + res.status)
      }
      await loadSettings()
    } catch (e) { alert('Failed to save system prompt: ' + e.message) }
    finally { setSavingSystemPrompt(false) }
  }

  async function saveModelSettings() {
    if (availableModels.length === 0) { alert('No models loaded yet.'); return }
    if (!defaultModel) { alert('Default model is required.'); return }
    if (!availableModels.includes(defaultModel)) {
      alert('Default model must be one of the available models.')
      return
    }
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ default_model: defaultModel }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'HTTP ' + res.status)
      }
      await loadSettings()
      alert('Model settings saved.')
    } catch (e) { alert('Failed to save model settings: ' + e.message) }
  }

  function showSkillForm(skill) {
    setSkillForm(skill !== undefined ? { skill } : {})
    setForm({ name: skill?.name ?? '', description: skill?.description ?? '', policy: skill?.policy ?? '', env: (skill?.env ?? []).join(', ') })
    setPolicyPopupOpen(false)
    setPolicyDesc('')
  }

  function hideSkillForm() {
    setSkillForm(null)
    setPolicyPopupOpen(false)
  }

  function _parseEnvList(raw) {
    return raw.split(',').map(s => s.trim()).filter(Boolean)
  }

  async function createSkill() {
    if (!form.name.trim() || !form.description.trim()) { alert('Name and description are required.'); return }
    const envList = _parseEnvList(form.env)
    const res = await fetch('/api/skills', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: form.name.trim(), description: form.description.trim(), policy: form.policy.trim() || null, env: envList.length ? envList : null }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => null)
      alert(err?.detail || 'Failed to create skill.')
      return
    }
    hideSkillForm()
    await loadSkills()
  }

  async function saveSkill(id) {
    if (!form.name.trim() || !form.description.trim()) { alert('Name and description are required.'); return }
    const envList = _parseEnvList(form.env)
    const res = await fetch('/api/skills/' + id, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: form.name.trim(), description: form.description.trim(), policy: form.policy.trim() || null, env: envList }),
    })
    if (!res.ok) { alert('Failed to save skill.'); return }
    hideSkillForm()
    await loadSkills()
  }

  async function toggleSkill(skill) {
    const res = await fetch('/api/skills/' + skill.id, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !skill.enabled }),
    })
    if (!res.ok) { alert('Failed to toggle skill.'); return }
    await loadSkills()
  }

  async function deleteSkill(id) {
    if (!confirm('Delete this skill?')) return
    const res = await fetch('/api/skills/' + id, { method: 'DELETE' })
    if (!res.ok) { alert('Failed to delete skill.'); return }
    await loadSkills()
  }

  async function syncRemoteSkills() {
    setSyncing(true)
    try {
      const res = await fetch('/api/skills/sync', { method: 'POST' })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'HTTP ' + res.status)
      }
      await loadSkills()
    } catch (e) { alert('Sync failed: ' + e.message) }
    finally { setSyncing(false) }
  }

  async function loadSecrets() {
    try {
      const res = await fetch('/api/secrets')
      if (res.ok) setSecrets(await res.json())
    } catch {}
  }

  async function addSecret() {
    if (!newSecretName.trim() || !newSecretValue.trim()) { alert('Name and value are required.'); return }
    const res = await fetch('/api/secrets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newSecretName.trim(), value: newSecretValue.trim() }),
    })
    if (!res.ok) { alert('Failed to add secret.'); return }
    setNewSecretName('')
    setNewSecretValue('')
    await loadSecrets()
  }

  async function deleteSecret(name) {
    if (!confirm(`Delete secret "${name}"?`)) return
    const res = await fetch('/api/secrets/' + encodeURIComponent(name), { method: 'DELETE' })
    if (!res.ok) { alert('Failed to delete secret.'); return }
    await loadSecrets()
  }

  async function generatePolicy() {
    if (!policyDesc.trim()) { alert('Please describe the policy first.'); return }
    setGenerating(true)
    try {
      const res = await fetch('/api/skills/generate-policy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skill_name: form.name,
          skill_description: form.description,
          description: policyDesc.trim(),
        }),
      })
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}))
        throw new Error(errBody.detail || 'HTTP ' + res.status)
      }
      const data = await res.json()
      setForm(prev => ({
        ...prev,
        policy: prev.policy.trim() ? prev.policy.trim() + '\n\n' + data.policy : data.policy,
      }))
      setPolicyPopupOpen(false)
      setPolicyDesc('')
    } catch (e) { alert('Failed to generate policy: ' + e.message) }
    finally { setGenerating(false) }
  }

  const isEdit = skillForm !== null && 'skill' in skillForm

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal">
        <div className="modal-header">
          <h2>Skills &amp; Settings</h2>
          <button className="btn-close" onClick={onClose}>&#x2715;</button>
        </div>

        <div className="modal-body">
          {/* Settings */}
          <div className="settings-section">
            <label>
              <input
                type="checkbox"
                checked={approvalRequired}
                onChange={e => onApprovalToggle(e.target.checked)}
                style={{ accentColor: '#a371f7', cursor: 'pointer' }}
              />
              Require approval before each command
            </label>
            {!isAdmin && globalApprovalRequired && (
              <div className="settings-help" style={{ marginTop: '4px' }}>
                ℹ️ Approval is also enforced globally by your administrator.
              </div>
            )}
          </div>

          {/* API Keys */}
          <div className="settings-section">
            <div className="settings-field-title" style={{ marginBottom: '6px' }}>API Keys</div>
            {newlyCreatedKey && (
              <div className="apikey-reveal">
                <div className="settings-help" style={{ marginBottom: '4px', color: 'var(--text-muted)' }}>
                  ⚠ Copy your key now — it will not be shown again.
                </div>
                <div className="apikey-reveal-row">
                  <code className="apikey-value">{newlyCreatedKey.key}</code>
                  <button className="btn-sm btn-secondary" onClick={copyKey}>
                    {keyCopied ? '✓ Copied' : 'Copy'}
                  </button>
                </div>
                <div style={{ marginTop: '4px' }}>
                  <button className="btn-sm btn-secondary" onClick={() => setNewlyCreatedKey(null)}>Dismiss</button>
                </div>
              </div>
            )}
            {apiKeys.length > 0 ? (
              <div>
                {apiKeys.map(k => (
                  <div key={k.key_id} className="skill-card">
                    <div className="skill-info">
                      <div className="skill-name" style={{ fontSize: '0.8rem' }}>{k.name}</div>
                      <div className="skill-desc">
                        {k.key_prefix}…
                        {' · '}
                        {new Date(k.created_at).toLocaleDateString()}
                        {k.last_used_at && ` · last used ${new Date(k.last_used_at).toLocaleDateString()}`}
                      </div>
                    </div>
                    <div className="skill-actions">
                      <button className="btn-sm btn-danger" onClick={() => revokeApiKey(k.key_id, k.name)}>Revoke</button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              !newlyCreatedKey && <div className="settings-help">No API keys yet.</div>
            )}
            <form onSubmit={createApiKey} style={{ display: 'flex', gap: '6px', marginTop: '4px' }}>
              <input
                type="text"
                placeholder="Key name (e.g. my-script)"
                value={newKeyName}
                onChange={e => setNewKeyName(e.target.value)}
                style={{ flex: 1, background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--text)', padding: '0.4rem 0.6rem', fontFamily: 'inherit', fontSize: '0.75rem' }}
              />
              <button type="submit" className="btn-sm btn-secondary" disabled={creatingKey || !newKeyName.trim()}>
                {creatingKey ? 'Creating…' : '+ New key'}
              </button>
            </form>
          </div>

          {/* Change password */}
          <div className="settings-section">
            <div className="settings-field-title" style={{ marginBottom: '6px' }}>Change password</div>
            <form onSubmit={changePassword} style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <input
                type="password"
                placeholder="Current password"
                value={currentPassword}
                onChange={e => { setCurrentPassword(e.target.value); setPasswordError(''); setPasswordSuccess(false) }}
                required
              />
              <input
                type="password"
                placeholder="New password"
                value={newPassword}
                onChange={e => { setNewPassword(e.target.value); setPasswordError(''); setPasswordSuccess(false) }}
                required
              />
              {passwordError && <div className="settings-error">{passwordError}</div>}
              {passwordSuccess && <div className="settings-help" style={{ color: 'var(--green, #3fb950)' }}>Password changed.</div>}
              <div className="form-actions" style={{ marginTop: 0 }}>
                <button type="submit" className="btn-sm btn-secondary" disabled={changingPassword}>
                  {changingPassword ? 'Saving…' : 'Change password'}
                </button>
              </div>
            </form>
          </div>

          {isAdmin && (
          <div className="settings-section">
            <div className="settings-field">
              <div className="settings-field-title">LLM endpoint</div>
              <input
                type="text"
                value={llmEndpoint}
                onChange={e => setLlmEndpoint(e.target.value)}
                placeholder="https://api.example.com/v1"
              />
            </div>
            <div className="settings-field">
              <div className="settings-field-title">API token</div>
              <input
                type="password"
                value={llmToken}
                onChange={e => {
                  setLlmToken(e.target.value)
                  if (e.target.value !== MASKED_TOKEN_VALUE) setTokenMasked(false)
                }}
                onFocus={() => {
                  if (tokenMasked) { setLlmToken(''); setTokenMasked(false) }
                }}
                placeholder="Leave empty to keep current token"
              />
              <div className="settings-help">{tokenHelp}</div>
            </div>
            <div className="form-actions" style={{ marginTop: 0 }}>
              <button className="btn-sm btn-secondary" onClick={saveLlmSettings}>Save LLM settings</button>
            </div>
          </div>
          )}

          {/* Remote skill repository */}
          {isAdmin && (
          <div className="settings-section">
            <div className="settings-field">
              <div className="settings-field-title">Remote skill repository URL</div>
              <input
                type="text"
                value={repoUrl}
                onChange={e => setRepoUrl(e.target.value)}
                placeholder="https://github.com/org/skills-repo"
              />
            </div>
            <div className="settings-field">
              <div className="settings-field-title">Branch</div>
              <input
                type="text"
                value={repoBranch}
                onChange={e => setRepoBranch(e.target.value)}
                placeholder="main"
              />
            </div>
            {repoSaveError && (
              <div className="settings-error">{repoSaveError}</div>
            )}
            <div className="form-actions" style={{ marginTop: 0 }}>
              <button className="btn-sm btn-secondary" onClick={saveRepoSettings} disabled={savingRepo}>
                {savingRepo ? 'Saving\u2026' : 'Save repo settings'}
              </button>
            </div>
          </div>
          )}

          {/* Model configuration */}
          {isAdmin && (
          <div className="settings-section">
            <div className="settings-field">
              <div className="settings-field-title">Available models</div>
              {fetchingModels && <div className="settings-help">Fetching models…</div>}
              {fetchModelsError && (
                <div className="settings-error">{fetchModelsError}</div>
              )}
              {!fetchingModels && availableModels.length > 0 && (
                <ul style={{ margin: '8px 0 0', padding: 0, listStyle: 'none', fontSize: '0.85rem', color: 'var(--text-muted, #aaa)' }}>
                  {availableModels.map(m => <li key={m}>{m}</li>)}
                </ul>
              )}
            </div>
            <div className="settings-field">
              <div className="settings-field-title">Default model</div>
              {availableModels.length > 0 ? (
                <select
                  value={defaultModel}
                  onChange={e => setDefaultModel(e.target.value)}
                >
                  {availableModels.map(m => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              ) : (
                <div className="settings-help">Fetch models from the API to select a default.</div>
              )}
            </div>
            <div className="form-actions" style={{ marginTop: 0 }}>
              <button className="btn-sm btn-secondary" onClick={saveModelSettings} disabled={availableModels.length === 0}>
                Save model settings
              </button>
            </div>
          </div>
          )}

          {/* System prompt */}
          {isAdmin && (
          <div className="settings-section">
            <div className="settings-field">
              <div className="settings-field-title">System prompt</div>
              <textarea
                value={systemPrompt}
                onChange={e => setSystemPrompt(e.target.value)}
                placeholder="Custom instructions prepended to every agent conversation…"
                rows={5}
                style={{ width: '100%', resize: 'vertical' }}
              />
              <div className="settings-help">Prepended to the agent’s built-in prompt on every run.</div>
            </div>
            <div className="form-actions" style={{ marginTop: 0 }}>
              <button className="btn-sm btn-secondary" onClick={saveSystemPrompt} disabled={savingSystemPrompt}>
                {savingSystemPrompt ? 'Saving…' : 'Save system prompt'}
              </button>
            </div>
          </div>
          )}

          {/* Remote skill detail view */}
          {detailSkill !== null && (
            <div className="skill-form" data-testid="remote-skill-detail">
              <div>
                <label>Tool name</label>
                <div className="skill-detail-value">{detailSkill.name}</div>
              </div>
              <div>
                <label>Description</label>
                <div className="skill-detail-value">{detailSkill.description}</div>
              </div>
              <div>
                <label>Policy (Rego rules)</label>
                {detailSkill.policy
                  ? <pre className="skill-detail-policy">{detailSkill.policy}</pre>
                  : <div className="skill-detail-value">No policy set — falls through to the global policy.</div>
                }
              </div>
              <div className="form-actions">
                <button className="btn-sm btn-secondary" onClick={() => setDetailSkill(null)}>Close</button>
              </div>
            </div>
          )}

          {/* Skill form */}
          {skillForm !== null && (
            <div className="skill-form">
              <label>Tool name</label>
              <input
                type="text"
                value={form.name}
                onChange={e => setForm(p => ({ ...p, name: e.target.value }))}
                placeholder="e.g. kubectl"
              />
              <label>Description</label>
              <textarea
                value={form.description}
                onChange={e => setForm(p => ({ ...p, description: e.target.value }))}
                placeholder="Describe how the agent should use this tool\u2026"
              />
              <label>Environment variables (comma-separated names injected at runtime)</label>
              <input
                type="text"
                value={form.env}
                onChange={e => setForm(p => ({ ...p, env: e.target.value }))}
                placeholder="e.g. API_TOKEN, AUTH_HEADER"
              />
              <label>Policy (Rego rules \u2014 leave empty to disable)</label>
              <textarea
                value={form.policy}
                onChange={e => setForm(p => ({ ...p, policy: e.target.value }))}
                style={{ fontFamily: 'ui-monospace, monospace' }}
              />
              <button type="button" className="btn-sm btn-secondary" onClick={() => setPolicyPopupOpen(v => !v)}>
                Generate with AI
              </button>
              {policyPopupOpen && (
                <div className="policy-popup">
                  <label>Describe the policy in plain English</label>
                  <textarea
                    value={policyDesc}
                    onChange={e => setPolicyDesc(e.target.value)}
                    placeholder="e.g. only allow kubectl get and kubectl describe commands"
                    rows={3}
                  />
                  <div className="form-actions">
                    <button type="button" className="btn-sm btn-secondary" onClick={generatePolicy} disabled={generating}>
                      {generating ? 'Generating\u2026' : 'Generate'}
                    </button>
                    <button type="button" className="btn-sm btn-secondary" onClick={() => setPolicyPopupOpen(false)}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}
              <div className="form-actions">
                <button className="btn-sm btn-secondary" onClick={isEdit ? () => saveSkill(skillForm.skill.id) : createSkill}>
                  {isEdit ? 'Save' : 'Add Skill'}
                </button>
                <button className="btn-sm btn-secondary" onClick={hideSkillForm}>Cancel</button>
              </div>
            </div>
          )}

          {/* Skills list */}
          <div>
            {skills.length === 0
              ? <div className="no-items-msg">No skills yet.</div>
              : skills.map(skill => (
                  <div key={skill.id} className="skill-card">
                    <div className="skill-info">
                      <div className="skill-name">
                        {skill.name}
                        {skill.source === 'remote' && (
                          <span className="remote-badge">remote</span>
                        )}
                      </div>
                      <div className="skill-desc">{skill.description}</div>
                      <span className={'policy-badge ' + (skill.policy ? 'has-policy' : 'no-policy')}>
                        {skill.policy ? 'policy set' : 'no policy'}
                      </span>
                    </div>
                    <div className="skill-actions">
                      {skill.source === 'remote' ? (
                        <>
                          <span className={'toggle-enabled readonly' + (skill.enabled ? ' on' : '')}>
                            {skill.enabled ? 'enabled' : 'disabled'}
                          </span>
                          <button className="btn-sm btn-secondary" onClick={() => setDetailSkill(d => d?.id === skill.id ? null : skill)}>Details</button>
                        </>
                      ) : isAdmin ? (
                        <>
                          <button
                            className={'toggle-enabled' + (skill.enabled ? ' on' : '')}
                            onClick={() => toggleSkill(skill)}
                          >
                            {skill.enabled ? 'enabled' : 'disabled'}
                          </button>
                          <button className="btn-sm btn-secondary" onClick={() => showSkillForm(skill)}>Edit</button>
                          <button className="btn-sm btn-danger" onClick={() => deleteSkill(skill.id)}>Del</button>
                        </>
                      ) : (
                        <>
                          <span className={'toggle-enabled readonly' + (skill.enabled ? ' on' : '')}>
                            {skill.enabled ? 'enabled' : 'disabled'}
                          </span>
                          <button className="btn-sm btn-secondary" onClick={() => setDetailSkill(d => d?.id === skill.id ? null : skill)}>Details</button>
                        </>
                      )}
                    </div>
                  </div>
                ))
            }
          </div>

          <div className="modal-skills-footer">
            {isAdmin && (
              <button className="modal-add-btn" onClick={() => showSkillForm(undefined)}>+ Add skill</button>
            )}
            {isAdmin && skillsRepoConfigured && (
              <button className="btn-sm btn-secondary" onClick={syncRemoteSkills} disabled={syncing}>
                {syncing ? 'Syncing\u2026' : 'Sync remote skills'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
