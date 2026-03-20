# Command Approval Gate — Design Spec

**Date:** 2026-03-20
**Branch:** personal-companion
**Status:** Approved

## Overview

A global toggle that, when active, pauses the agent workflow before each command execution and requires explicit user approval via the UI. If the user denies (or the timeout expires), the command is not executed and the run is marked `policy_denied`.

## Architecture

### Server-side state

Three new module-level variables in `main.py`:

```python
_approval_required: bool = False
_settings_lock = threading.Lock()          # protects _approval_required

_pending_approvals: dict[str, PendingApproval] = {}
_pending_approvals_lock = threading.Lock() # protects _pending_approvals
```

A `PendingApproval` dataclass:
```python
@dataclass
class PendingApproval:
    run_id: str
    command: Command
    event: threading.Event
    approved: bool = False
```

### Settings endpoints

- `GET /api/settings` → `{ "approval_required": bool }` — reads `_approval_required` under `_settings_lock`
- `PUT /api/settings` → accepts `{ "approval_required": bool }`, writes `_approval_required` under `_settings_lock`

### RunContext shared reference

`AgentWorkflow.run()` currently creates its own `RunContext` internally. The approval gate needs to mutate the same object stored in `_runs[run_id]` so the poll endpoint sees updated state.

The fix: `AgentWorkflow.run()` gains an optional `ctx: RunContext | None = None` parameter. When provided, the method uses it directly instead of constructing a new `RunContext`, and **must return that same object**. This is a hard functional correctness requirement: without it, any approval gate fired after the first iteration mutates an object no longer stored in `_runs`, making subsequent approval requests invisible to the UI poll loop.

When `ctx` is provided, the `run_id` parameter of `workflow.run()` is redundant (logging uses `ctx.run_id`). It must not be used to construct a new `RunContext`. The manual-run `_execute` therefore calls `workflow.run(prompt=..., ctx=ctx)` — `run_id` is omitted from the call when `ctx` is provided.

The new `AgentWorkflow.run()` signature:
```python
def run(
    self,
    prompt: str,
    run_id: str | None = None,
    max_iterations: int = 10,
    ctx: RunContext | None = None,
) -> RunContext:
    if ctx is None:
        assert run_id is not None
        ctx = RunContext(run_id=run_id, prompt=prompt)
    # ... rest of loop ...
    return ctx
```

This keeps the webhook call site (`workflow.run(prompt=..., run_id=run_id, max_iterations=...)`) unchanged.

Webhook runs do not pass a pre-created `ctx` — they let the workflow create its own as before.

### AgentWorkflow constructor

```python
class AgentWorkflow:
    def __init__(
        self,
        planner: Planner,
        policy: PolicyEvaluator,
        executor: CommandExecutor,
        approval_gate: Callable[[Command], bool] | None = None,
    ) -> None:
        self._planner = planner
        self._policy = policy
        self._executor = executor
        self._approval_gate = approval_gate
```

### Approval gate injection

`_build_workflow()` signature changes to accept optional `run_id` and `ctx` for manual runs. Both must be provided together or not at all (assert `(run_id is None) == (ctx is None)` to catch misconfiguration):

```python
def _build_workflow(skill_repo, run_id=None, ctx=None):
    # Gate is only possible when both run_id and ctx are provided (manual runs).
    # Webhook runs pass run_id but no ctx — gate is always None for them.
    gate = None
    if run_id is not None and ctx is not None:
        with _settings_lock:
            need_approval = _approval_required
        if need_approval:
            gate = _make_approval_gate(run_id, ctx)
    return AgentWorkflow(
        planner=Planner(skill_repo),
        policy=PolicyEvaluator(skills=...),
        executor=CommandExecutor(),
        approval_gate=gate,
    )
```

Manual-run `_execute()`:
```python
def _execute():
    workflow = _build_workflow(skill_repo, run_id=run_id, ctx=ctx)
    result = workflow.run(prompt=request.prompt, ctx=ctx)  # run_id omitted; ctx.run_id is used internally
    with _runs_lock:
        _runs[run_id] = result  # result is ctx (same object); ensures _runs is up to date
```

Webhook `_execute()` remains unchanged — `_build_workflow(skill_repo)` with no extra args, gate always `None`. **This is intentional**: webhook-triggered runs bypass the approval gate by design (no human is watching them interactively). See "Out of scope".

The approval mode is snapshotted per-run at start time. A toggle change mid-run does not affect the current run.

### Approval gate closure (`_make_approval_gate`)

```python
def _make_approval_gate(run_id: str, ctx: RunContext):
    def gate(command: Command) -> bool:
        approval = PendingApproval(
            run_id=run_id,
            command=command,
            event=threading.Event(),
        )
        with _pending_approvals_lock:
            _pending_approvals[run_id] = approval

        # Write pending_command BEFORE status. The UI only shows the approval section
        # when BOTH status=="waiting_approval" AND pending_command is non-None.
        # Write ordering guarantees: a poll that sees status=="waiting_approval" will
        # always see a non-None pending_command. The only possible torn state is
        # pending_command set with status still "running" — the UI ignores pending_command
        # in that case and shows a normal running card. This is safe and transient.
        ctx.pending_command = command.model_dump()
        ctx.status = "waiting_approval"

        timed_out = not approval.event.wait(timeout=300)  # block up to 5 minutes

        if timed_out:
            # Clean up the pending entry so stale approve/deny calls return 404
            with _pending_approvals_lock:
                _pending_approvals.pop(run_id, None)
            # Use timed_out as the authoritative signal — return False regardless of
            # approval.approved. This prevents an approve-at-timeout race where the
            # approve endpoint sets approved=True after event.wait() has already
            # timed out, which would otherwise let the command execute.
            ctx.pending_command = None
            return False

        # Not timed out — user responded via approve/deny endpoint
        ctx.pending_command = None
        if approval.approved:
            ctx.status = "running"
        # else: leave as "waiting_approval"; workflow sets "policy_denied" immediately after

        return approval.approved
    return gate
```

### RunContext changes

```python
class RunContext(BaseModel):
    # existing fields ...
    status: Literal["running", "done", "failed", "policy_denied", "waiting_approval"] = "running"
    pending_command: dict[str, Any] | None = None
```

`pending_command` is populated with `command.model_dump()` while waiting, cleared after the decision. One pending approval per run at a time is an invariant of the sequential workflow loop.

### Approval decision endpoints

- `POST /api/agent/runs/{run_id}/approve`:
  1. Acquire `_pending_approvals_lock`
  2. Pop the entry; return 404 if not present
  3. Set `approval.approved = True`
  4. Call `approval.event.set()`

- `POST /api/agent/runs/{run_id}/deny`:
  1. Acquire `_pending_approvals_lock`
  2. Pop the entry; return 404 if not present
  3. `approval.approved` stays `False` (default)
  4. Call `approval.event.set()`

Both return 404 for any `run_id` with no pending approval (unknown run, already resolved, timed out, or not waiting). Callers treat 404 as a no-op.

### Summarization after denial

```python
if ctx.status == "policy_denied":
    ctx.final_message = "Run stopped: a command was not approved."
else:
    try:
        ctx.final_message = planner.summarize(ctx)
    except Exception as exc:
        _log("summarize_failed", ...)
```

This replaces the existing unconditional `summarize()` call and applies to both OPA and human denials.

## Workflow loop (updated pseudocode)

```
for iteration in range(max_iterations):
    plan_output = planner.next_action(ctx)
    if plan_output.status in ("done", "failed"):
        ctx.status = plan_output.status
        break

    command = plan_output.command

    # OPA gate (existing)
    allowed, reason, skill_name = policy.evaluate(command)
    if not allowed:
        ctx.status = "policy_denied"; break

    # Human approval gate (new, only when self._approval_gate is not None)
    if self._approval_gate and not self._approval_gate(command):
        ctx.status = "policy_denied"; break

    result = executor.run(command)
    ctx.history.append(result)

# Summarization
if ctx.status == "policy_denied":
    ctx.final_message = "Run stopped: a command was not approved."
else:
    try:
        ctx.final_message = planner.summarize(ctx)
    except Exception as exc:
        _log("summarize_failed", ...)
```

## UI changes

### Toggle

In the Manual Runs tab, next to the "OPA policy active" hint in the query box footer:

```
[toggle] Require approval before each command
```

- Reads current value from `GET /api/settings` on page load; defaults to unchecked if the request fails
- PUTs new value on toggle change
- Note: webhook-triggered runs always bypass the approval gate regardless of this setting

### Status pill

New CSS class `s-waiting_approval` — purple (`background: #2d1f5e; color: #a371f7`, same as `s-queued`). The pill renderer does `'status-pill s-' + run.status` so the class resolves automatically.

The spinner condition must include `waiting_approval`:
```js
const isActive = run.status === 'running' || run.status === 'waiting_approval';
if (isActive) pill.innerHTML = '<span class="spin"></span>';
```

### Approval section in run cards

When `run.status === "waiting_approval"` and `run.pending_command` is set, show a new section **above executor history**:

```
AWAITING APPROVAL
$ <argv joined>
<rationale text>
[ Approve ]  [ Deny ]
```

- Approve → `POST /api/agent/runs/{run_id}/approve`, then immediately re-poll
- Deny → `POST /api/agent/runs/{run_id}/deny`, then immediately re-poll
- Both buttons disabled after click to prevent double-submission

### Poll loop — waiting_approval must not terminate polling

```js
if (data.status !== 'running' && data.status !== 'waiting_approval') break;
```

### Initial load — resume polling for waiting_approval runs

```js
if (r.status === 'running' || r.status === 'waiting_approval') pollRun(r.run_id);
```

### Empty-state text for waiting_approval

```js
run.status === 'running' || run.status === 'waiting_approval'
  ? 'Agent is thinking…'
  : 'No commands were executed.'
```

### Webhook runs

Webhook-triggered runs always bypass the approval gate. The gate closure is never passed to `AgentWorkflow` for webhook execution paths.

## Error cases

| Scenario | Behaviour |
|---|---|
| Timeout (5 min, no response) | Gate cleans up entry, returns False → `policy_denied`, hardcoded final message |
| User denies | Gate returns False → `policy_denied`, hardcoded final message |
| Server restart while waiting | `_runs` is cleared; client re-polls and gets 404 |
| Double-click approve/deny | Second POST returns 404 — client ignores |
| Approve/deny on non-waiting run | 404 — client ignores |
| Toggle changed mid-run | No effect; gate is snapshotted per-run at start |
| Page reload while awaiting approval | `loadAll()` restores the card and resumes polling |

## Out of scope

- Persisting approval state across restarts
- Per-run approval toggle (global only)
- Webhook runs requiring approval
- Concurrent commands within a single run
