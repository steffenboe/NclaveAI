import { useState } from 'react'

export default function CommandCard({ action }) {
  const argv = (action.command?.argv ?? []).join(' ')
  const hasOutput = action.stdout || action.stderr
  const [outputOpen, setOutputOpen] = useState(true)

  return (
    <div className="cmd-card">
      <div className="cmd-head">
        <span className="cmd-argv">{argv}</span>
        {!action.allowed
          ? <span className="badge-denied">DENIED</span>
          : <>
              {action.exit_code !== null && action.exit_code !== undefined && (
                <span className={action.exit_code === 0 ? 'badge-ok' : 'badge-err'}>
                  exit {action.exit_code}
                </span>
              )}
              <span className={'skill-tag' + (action.skill_name ? '' : ' fallback')}>
                {action.skill_name ? 'skill: ' + action.skill_name : 'global policy'}
              </span>
            </>
        }
      </div>

      {action.command?.rationale && (
        <div className="cmd-rationale">\u21b3 {action.command.rationale}</div>
      )}

      {hasOutput && (
        <div className="cmd-output">
          <button className="cmd-output-toggle" onClick={() => setOutputOpen(v => !v)}>
            {outputOpen ? '▾ output' : '▸ output'}
          </button>
          {outputOpen && (
            <>
              {action.stdout && <pre>{action.stdout}</pre>}
              {action.stderr && <pre className="stderr">{action.stderr}</pre>}
            </>
          )}
        </div>
      )}
    </div>
  )
}
