export default function ApprovalCard({ run, onApprove, onDeny, pending }) {
  const argv = (run.pending_command?.argv ?? []).join(' ')

  return (
    <div className="approval-card">
      <div className="approval-header">
        <span className="approval-dot" />
        {' Awaiting your approval'}
      </div>
      <div className="approval-body">
        <div className="approval-cmd">{argv}</div>
        <div className="approval-rationale">\u21b3 {run.pending_command?.rationale}</div>
        <div className="approval-actions">
          <button className="btn-approve" onClick={onApprove} disabled={pending}>Approve</button>
          <button className="btn-deny" onClick={onDeny} disabled={pending}>Deny</button>
        </div>
      </div>
    </div>
  )
}
