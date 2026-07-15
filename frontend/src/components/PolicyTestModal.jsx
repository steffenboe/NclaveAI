import { useState, useEffect } from 'react'
import { useAuth } from '../AuthContext'
import { apiFetch as fetch } from '../apiFetch'

export default function PolicyTestModal({ onClose }) {
  const { user } = useAuth()
  const [regoPolicy, setRegoPolicy] = useState('')
  const [testCommand, setTestCommand] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [testCases, setTestCases] = useState([])
  const [loadingHistory, setLoadingHistory] = useState(false)

  useEffect(() => {
    loadTestCases()
  }, [])

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  async function loadTestCases() {
    setLoadingHistory(true)
    try {
      const res = await fetch('/api/admin/policy-test')
      if (res.ok) {
        const data = await res.json()
        setTestCases(data)
      }
    } catch (e) {
      console.error('Failed to load test cases:', e)
    } finally {
      setLoadingHistory(false)
    }
  }

  async function runTest(e) {
    e.preventDefault()
    if (!regoPolicy.trim() || !testCommand.trim()) {
      setError('Both policy and command are required')
      return
    }

    setRunning(true)
    setError('')
    setResult(null)

    try {
      const res = await fetch('/api/admin/policy-test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          rego_policy: regoPolicy,
          test_command: testCommand,
        }),
      })

      if (!res.ok) {
        const data = await res.json()
        setError(data.detail || 'Failed to run test')
        return
      }

      const data = await res.json()
      setResult(data)
      loadTestCases() // Refresh history
    } catch (e) {
      setError(e.message || 'Failed to run test')
    } finally {
      setRunning(false)
    }
  }

  async function deleteTestCase(testId) {
    try {
      const res = await fetch(`/api/admin/policy-test/${testId}`, {
        method: 'DELETE',
      })
      if (res.ok) {
        loadTestCases()
      }
    } catch (e) {
      console.error('Failed to delete test case:', e)
    }
  }

  function loadTestCase(tc) {
    setRegoPolicy(tc.rego_policy)
    setTestCommand(tc.test_command)
    setResult(null)
    setError('')
  }

  return (
    <div className="modal-overlay">
      <div className="modal policy-test-modal">
        <div className="modal-header">
          <h2>Policy Test</h2>
          <button className="btn-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body">
          <form onSubmit={runTest}>
            <div className="form-group">
              <label htmlFor="rego-policy">Rego Policy</label>
              <textarea
                id="rego-policy"
                className="rego-textarea"
                value={regoPolicy}
                onChange={(e) => setRegoPolicy(e.target.value)}
                placeholder="allow { input.argv[0] == &quot;kubectl&quot; }"
                rows={10}
                spellCheck={false}
              />
            </div>

            <div className="form-group">
              <label htmlFor="test-command">Test Command</label>
              <input
                id="test-command"
                type="text"
                value={testCommand}
                onChange={(e) => setTestCommand(e.target.value)}
                placeholder="kubectl get pods"
              />
            </div>

            {error && (
              <div className="error-message">
                {error}
              </div>
            )}

            <div className="form-actions">
              <button type="submit" className="btn-primary" disabled={running}>
                {running ? 'Running...' : 'Run Test'}
              </button>
            </div>
          </form>

          {result && (
            <div className="test-result">
              <h3>Result</h3>
              <div className={`result-badge ${result.allowed ? 'allowed' : 'denied'}`}>
                {result.allowed ? '✓ ALLOW' : '✗ DENY'}
              </div>
              
              {result.error && (
                <div className="result-error">
                  <h4>Error</h4>
                  <pre>{result.error}</pre>
                </div>
              )}

              {result.explanation && (
                <div className="result-explanation">
                  <h4>Explanation</h4>
                  <pre>{JSON.stringify(result.explanation, null, 2)}</pre>
                </div>
              )}
            </div>
          )}

          <div className="test-history">
            <h3>Recent Tests</h3>
            {loadingHistory ? (
              <div className="loading">Loading...</div>
            ) : testCases.length === 0 ? (
              <div className="empty-state">No test cases yet</div>
            ) : (
              <ul className="test-case-list">
                {testCases.map((tc) => (
                  <li key={tc.test_id} className="test-case-item">
                    <div className="test-case-info">
                      <code className="test-case-command">{tc.test_command}</code>
                      <span className="test-case-time">
                        {new Date(tc.created_at).toLocaleString()}
                      </span>
                    </div>
                    <div className="test-case-actions">
                      <button
                        className="btn-sm btn-secondary"
                        onClick={() => loadTestCase(tc)}
                      >
                        Load
                      </button>
                      <button
                        className="btn-sm btn-danger"
                        onClick={() => deleteTestCase(tc.test_id)}
                      >
                        Delete
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
