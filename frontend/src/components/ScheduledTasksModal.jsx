import { useEffect, useRef, useState } from 'react'

export default function ScheduledTasksModal({ onClose, onRunCreated }) {
  const backdropMouseDownRef = useRef(false)
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [showCreate, setShowCreate] = useState(false)
  const [newPrompt, setNewPrompt] = useState('')
  const [newCron, setNewCron] = useState('*/15 * * * *')
  const [newTimezone, setNewTimezone] = useState('UTC')
  const [creating, setCreating] = useState(false)

  const [editingId, setEditingId] = useState(null)
  const [editPrompt, setEditPrompt] = useState('')
  const [editCron, setEditCron] = useState('')
  const [editTimezone, setEditTimezone] = useState('UTC')
  const [editEnabled, setEditEnabled] = useState(true)
  const [savingEdit, setSavingEdit] = useState(false)

  useEffect(() => {
    loadTasks()
  }, [])

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  async function loadTasks() {
    setLoading(true)
    setError('')
    try {
      const res = await fetch('/api/scheduled-tasks')
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setError(data.detail || 'Failed to load scheduled tasks')
        return
      }
      setTasks(await res.json())
    } catch {
      setError('Failed to load scheduled tasks')
    } finally {
      setLoading(false)
    }
  }

  async function createTask(e) {
    e.preventDefault()
    if (!newPrompt.trim() || !newCron.trim() || !newTimezone.trim()) {
      alert('Prompt, cron, and timezone are required.')
      return
    }
    setCreating(true)
    try {
      const res = await fetch('/api/scheduled-tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: newPrompt.trim(),
          cron: newCron.trim(),
          timezone: newTimezone.trim(),
          enabled: true,
        }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to create scheduled task')
      }
      setNewPrompt('')
      setNewCron('*/15 * * * *')
      setNewTimezone('UTC')
      setShowCreate(false)
      await loadTasks()
    } catch (e2) {
      alert(e2.message)
    } finally {
      setCreating(false)
    }
  }

  function startEdit(task) {
    setEditingId(task.task_id)
    setEditPrompt(task.prompt)
    setEditCron(task.cron)
    setEditTimezone(task.timezone)
    setEditEnabled(task.enabled)
  }

  async function saveEdit(taskId) {
    if (!editPrompt.trim() || !editCron.trim() || !editTimezone.trim()) {
      alert('Prompt, cron, and timezone are required.')
      return
    }
    setSavingEdit(true)
    try {
      const res = await fetch(`/api/scheduled-tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: editPrompt.trim(),
          cron: editCron.trim(),
          timezone: editTimezone.trim(),
          enabled: editEnabled,
        }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to save scheduled task')
      }
      setEditingId(null)
      await loadTasks()
    } catch (e2) {
      alert(e2.message)
    } finally {
      setSavingEdit(false)
    }
  }

  async function runNow(taskId) {
    try {
      const res = await fetch(`/api/scheduled-tasks/${taskId}/run`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to run scheduled task')
      }
      const data = await res.json()
      const task = tasks.find(t => t.task_id === taskId)
      if (onRunCreated && data?.run_id) {
        onRunCreated({ run_id: data.run_id, status: data.status, prompt: task?.prompt })
      }
      await loadTasks()
    } catch (e2) {
      alert(e2.message)
    }
  }

  async function toggleEnabled(task) {
    try {
      const res = await fetch(`/api/scheduled-tasks/${task.task_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !task.enabled }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to toggle scheduled task')
      }
      await loadTasks()
    } catch (e2) {
      alert(e2.message)
    }
  }

  async function removeTask(taskId) {
    if (!confirm('Delete this scheduled task?')) return
    try {
      const res = await fetch(`/api/scheduled-tasks/${taskId}`, { method: 'DELETE' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to delete scheduled task')
      }
      await loadTasks()
    } catch (e2) {
      alert(e2.message)
    }
  }

  return (
    <div
      className="modal-overlay"
      onMouseDown={e => {
        backdropMouseDownRef.current = e.target === e.currentTarget
      }}
      onClick={e => {
        if (backdropMouseDownRef.current && e.target === e.currentTarget) {
          onClose()
        }
        backdropMouseDownRef.current = false
      }}
    >
      <div className="modal scheduled-tasks-modal">
        <div className="modal-header">
          <h2>Scheduled Tasks</h2>
          <button className="btn-close" onClick={onClose} aria-label="Close">x</button>
        </div>

        <div className="modal-body">
          {error && <p className="modal-error">{error}</p>}

          <div className="users-toolbar">
            <button className="btn-secondary" onClick={() => setShowCreate(v => !v)}>
              {showCreate ? 'Cancel' : '+ New scheduled task'}
            </button>
          </div>

          {showCreate && (
            <form onSubmit={createTask} className="users-create-form">
              <textarea
                className="scheduled-task-prompt-input"
                placeholder="Prompt to execute"
                value={newPrompt}
                onChange={e => setNewPrompt(e.target.value)}
                rows={3}
                required
              />
              <input
                type="text"
                placeholder="Cron (e.g. */15 * * * *)"
                value={newCron}
                onChange={e => setNewCron(e.target.value)}
                required
              />
              <input
                type="text"
                placeholder="Timezone (e.g. UTC)"
                value={newTimezone}
                onChange={e => setNewTimezone(e.target.value)}
                required
              />
              <button type="submit" className="btn-primary" disabled={creating}>
                {creating ? 'Creating...' : 'Create'}
              </button>
            </form>
          )}

          {loading ? (
            <p className="users-loading">Loading...</p>
          ) : (
            <table className="users-table">
              <thead>
                <tr>
                  <th>Prompt</th>
                  <th>Cron</th>
                  <th>Timezone</th>
                  <th>Next run</th>
                  <th>Last run</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map(task => {
                  const isEditing = editingId === task.task_id
                  return (
                    <tr key={task.task_id}>
                      <td>
                        {isEditing ? (
                          <textarea
                            className="scheduled-task-prompt-input"
                            value={editPrompt}
                            onChange={e => setEditPrompt(e.target.value)}
                            rows={3}
                          />
                        ) : (
                          <span title={task.prompt}>{task.prompt}</span>
                        )}
                      </td>
                      <td>
                        {isEditing ? (
                          <input value={editCron} onChange={e => setEditCron(e.target.value)} />
                        ) : (
                          task.cron
                        )}
                      </td>
                      <td>
                        {isEditing ? (
                          <input value={editTimezone} onChange={e => setEditTimezone(e.target.value)} />
                        ) : (
                          task.timezone
                        )}
                      </td>
                      <td>{task.next_run_at || '-'}</td>
                      <td>{task.last_run_at || '-'}</td>
                      <td>
                        <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
                          {isEditing ? (
                            <>
                              <label style={{ display: 'inline-flex', gap: '0.25rem', alignItems: 'center', fontSize: '0.72rem' }}>
                                <input
                                  type="checkbox"
                                  checked={editEnabled}
                                  onChange={e => setEditEnabled(e.target.checked)}
                                />
                                enabled
                              </label>
                              <button className="btn-sm btn-secondary" onClick={() => saveEdit(task.task_id)} disabled={savingEdit}>Save</button>
                              <button className="btn-sm btn-secondary" onClick={() => setEditingId(null)}>Cancel</button>
                            </>
                          ) : (
                            <>
                              <button className="btn-sm btn-secondary" onClick={() => runNow(task.task_id)}>Run now</button>
                              <button className="btn-sm btn-secondary" onClick={() => startEdit(task)}>Edit</button>
                              <button className="btn-sm btn-secondary" onClick={() => toggleEnabled(task)}>
                                {task.enabled ? 'Disable' : 'Enable'}
                              </button>
                              <button className="btn-sm btn-danger" onClick={() => removeTask(task.task_id)}>Del</button>
                            </>
                          )}
                        </div>
                        {task.last_error && (
                          <div className="settings-error" style={{ marginTop: '0.25rem' }}>{task.last_error}</div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
