import { useState, useEffect } from 'react'

const MASKED_TOKEN_VALUE = '********'

export default function SkillsModal({ onClose }) {
  const [approvalRequired, setApprovalRequired] = useState(false)
  const [llmEndpoint, setLlmEndpoint] = useState('')
  const [llmToken, setLlmToken] = useState('')
  const [tokenMasked, setTokenMasked] = useState(false)
  const [tokenHelp, setTokenHelp] = useState('')
  const [skills, setSkills] = useState([])
  // skillForm: null = hidden, {} = new skill, { skill } = editing existing
  const [skillForm, setSkillForm] = useState(null)
  const [form, setForm] = useState({ name: '', description: '', policy: '' })
  const [policyPopupOpen, setPolicyPopupOpen] = useState(false)
  const [policyDesc, setPolicyDesc] = useState('')
  const [generating, setGenerating] = useState(false)

  useEffect(() => {
    loadSettings()
    loadSkills()
  }, [])

  useEffect(() => {
    function onKeyDown(e) { if (e.key === 'Escape') onClose() }
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
      if (data.has_llm_api_key) {
        setLlmToken(MASKED_TOKEN_VALUE)
        setTokenMasked(true)
        setTokenHelp('A token is currently configured and masked. Type a new one to replace it.')
      } else {
        setLlmToken('')
        setTokenMasked(false)
        setTokenHelp('No token configured yet.')
      }
    } catch {}
  }

  async function loadSkills() {
    try {
      const res = await fetch('/api/skills')
      if (res.ok) setSkills(await res.json())
    } catch {}
  }

  async function onApprovalToggle(checked) {
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approval_required: checked }),
      })
      if (res.ok) setApprovalRequired(checked)
    } catch {}
  }

  async function saveLlmSettings() {
    if (!llmEndpoint.trim()) { alert('LLM endpoint is required.'); return }
    const payload = { llm_base_url: llmEndpoint.trim() }
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
      alert('LLM settings saved.')
    } catch (e) { alert('Failed to save LLM settings: ' + e.message) }
  }

  function showSkillForm(skill) {
    setSkillForm(skill !== undefined ? { skill } : {})
    setForm({ name: skill?.name ?? '', description: skill?.description ?? '', policy: skill?.policy ?? '' })
    setPolicyPopupOpen(false)
    setPolicyDesc('')
  }

  function hideSkillForm() {
    setSkillForm(null)
    setPolicyPopupOpen(false)
  }

  async function createSkill() {
    if (!form.name.trim() || !form.description.trim()) { alert('Name and description are required.'); return }
    const res = await fetch('/api/skills', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: form.name.trim(), description: form.description.trim(), policy: form.policy.trim() || null }),
    })
    if (!res.ok) { alert('Failed to create skill.'); return }
    hideSkillForm()
    await loadSkills()
  }

  async function saveSkill(id) {
    if (!form.name.trim() || !form.description.trim()) { alert('Name and description are required.'); return }
    const res = await fetch('/api/skills/' + id, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: form.name.trim(), description: form.description.trim(), policy: form.policy.trim() || null }),
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
                      <div className="skill-name">{skill.name}</div>
                      <div className="skill-desc">{skill.description}</div>
                      <span className={'policy-badge ' + (skill.policy ? 'has-policy' : 'no-policy')}>
                        {skill.policy ? 'policy set' : 'no policy'}
                      </span>
                    </div>
                    <div className="skill-actions">
                      <button
                        className={'toggle-enabled' + (skill.enabled ? ' on' : '')}
                        onClick={() => toggleSkill(skill)}
                      >
                        {skill.enabled ? 'enabled' : 'disabled'}
                      </button>
                      <button className="btn-sm btn-secondary" onClick={() => showSkillForm(skill)}>Edit</button>
                      <button className="btn-sm btn-danger" onClick={() => deleteSkill(skill.id)}>Del</button>
                    </div>
                  </div>
                ))
            }
          </div>

          <button className="modal-add-btn" onClick={() => showSkillForm(undefined)}>+ Add skill</button>
        </div>
      </div>
    </div>
  )
}
