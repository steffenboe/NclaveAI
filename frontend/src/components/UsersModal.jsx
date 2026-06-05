import { useState, useEffect } from 'react'
import { useAuth } from '../AuthContext'

export default function UsersModal({ onClose }) {
  const { user: currentUser } = useAuth()
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // Create form
  const [showCreate, setShowCreate] = useState(false)
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newRole, setNewRole] = useState('user')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')

  useEffect(() => {
    loadUsers()
  }, [])

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  async function loadUsers() {
    setLoading(true)
    setError('')
    try {
      const res = await fetch('/api/users')
      if (!res.ok) { setError('Failed to load users'); return }
      setUsers(await res.json())
    } catch {
      setError('Failed to load users')
    } finally {
      setLoading(false)
    }
  }

  async function handleCreate(e) {
    e.preventDefault()
    setCreateError('')
    setCreating(true)
    try {
      const res = await fetch('/api/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: newUsername, password: newPassword, role: newRole }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        setCreateError(data.detail || 'Failed to create user')
        return
      }
      setNewUsername('')
      setNewPassword('')
      setNewRole('user')
      setShowCreate(false)
      await loadUsers()
    } catch {
      setCreateError('Failed to create user')
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(userId) {
    if (!confirm('Delete this user?')) return
    try {
      const res = await fetch(`/api/users/${userId}`, { method: 'DELETE' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        alert(data.detail || 'Failed to delete user')
        return
      }
      await loadUsers()
    } catch {
      alert('Failed to delete user')
    }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <h2>User Management</h2>
          <button className="btn-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="modal-body">
          {error && <p className="modal-error">{error}</p>}

          <div className="users-toolbar">
            <button
              className="btn-secondary"
              onClick={() => { setShowCreate(v => !v); setCreateError('') }}
            >
              {showCreate ? 'Cancel' : '+ New user'}
            </button>
          </div>

          {showCreate && (
            <form onSubmit={handleCreate} className="users-create-form">
              <input
                type="text"
                placeholder="Username"
                value={newUsername}
                onChange={e => setNewUsername(e.target.value)}
                required
                autoFocus
              />
              <input
                type="password"
                placeholder="Password"
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
                required
              />
              <select value={newRole} onChange={e => setNewRole(e.target.value)}>
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
              {createError && <span className="users-create-error">{createError}</span>}
              <button type="submit" className="btn-primary" disabled={creating}>
                {creating ? 'Creating…' : 'Create'}
              </button>
            </form>
          )}

          {loading
            ? <p className="users-loading">Loading…</p>
            : (
              <table className="users-table">
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Role</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {users.map(u => (
                    <tr key={u.user_id} className={u.user_id === currentUser?.user_id ? 'users-row-self' : ''}>
                      <td>{u.username}{u.user_id === currentUser?.user_id && <span className="users-you"> (you)</span>}</td>
                      <td><span className={`role-badge role-${u.role}`}>{u.role}</span></td>
                      <td>
                        {u.user_id !== currentUser?.user_id && (
                          <button
                            className="btn-delete-user"
                            onClick={() => handleDelete(u.user_id)}
                            title="Delete user"
                          >
                            🗑
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          }
        </div>
      </div>
    </div>
  )
}
