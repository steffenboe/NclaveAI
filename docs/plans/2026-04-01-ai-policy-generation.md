# AI Policy Generation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "Generate with AI" button to the skill form that lets users describe a policy in plain English and have the LLM generate Rego rule bodies appended to the policy textarea.

**Architecture:** A new `generate_policy()` method on `Planner` uses a dedicated LangChain prompt chain to produce bare Rego rule bodies. A new `POST /api/skills/generate-policy` endpoint exposes this over HTTP. The UI adds an inline popup with a textarea and Generate/Cancel buttons that calls the endpoint and appends the result to the existing policy textarea.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / LangChain (`langchain_openai`, `StrOutputParser`) / pytest / vanilla JS

---

## Context & Discoveries

- `app/planner.py` already has two prompt chains (`_chain` for next_action, `_summarize_chain` for summarize). Add `_policy_chain` as a third.
- Tests bypass `Planner.__init__` via `Planner.__new__(Planner)` and inject mock chains directly. Follow this pattern.
- OPA policy field stores only rule bodies — no `package` line. The evaluator prepends `package ops.agent` automatically via `interp.add_module("skill", f"package ops.agent\n{skill.policy}")`.
- **Route ordering matters:** `POST /api/skills/generate-policy` MUST be declared before `GET /api/skills/{skill_id}` in `main.py`, or FastAPI will try to handle `generate-policy` as a skill ID.
- Pre-existing LSP errors in `app/workflow.py`, `app/planner.py`, `app/main.py` — ignore these.

---

### Task 1: `generate_policy` method on `Planner`

**Files:**
- Modify: `app/planner.py`
- Test: `tests/test_planner.py`

**Step 1: Write the failing tests**

Add to `tests/test_planner.py`:

```python
def test_generate_policy_returns_string(tmp_path):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = 'allow { input.argv[0] == "kubectl" }'
    planner = Planner.__new__(Planner)
    planner._policy_chain = mock_chain
    result = planner.generate_policy(
        skill_name="kubectl",
        skill_description="Kubernetes CLI",
        plain_description="only allow kubectl commands",
    )
    assert isinstance(result, str)
    assert result == 'allow { input.argv[0] == "kubectl" }'


def test_generate_policy_passes_all_context_to_chain(tmp_path):
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = "allow { true }"
    planner = Planner.__new__(Planner)
    planner._policy_chain = mock_chain
    planner.generate_policy(
        skill_name="gh",
        skill_description="GitHub CLI",
        plain_description="allow all gh commands",
    )
    call_kwargs = mock_chain.invoke.call_args[0][0]
    assert call_kwargs["skill_name"] == "gh"
    assert call_kwargs["skill_description"] == "GitHub CLI"
    assert call_kwargs["plain_description"] == "allow all gh commands"
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_planner.py::test_generate_policy_returns_string tests/test_planner.py::test_generate_policy_passes_all_context_to_chain -v
```
Expected: FAIL with `AttributeError: 'Planner' object has no attribute 'generate_policy'`

**Step 3: Add constants and method to `app/planner.py`**

After the existing `_SUMMARIZE_HUMAN_PROMPT` constant, add:

```python
_POLICY_SYSTEM_PROMPT = """\
You are an OPA (Open Policy Agent) Rego expert.

Your task: generate a valid Rego policy body for the given skill.

STRICT RULES:
- Output ONLY bare Rego rule bodies. No `package` line. No markdown fences. No explanation.
- The input object has exactly one field: `input.argv` — a list of strings (the command + arguments).
- Use `allow { ... }` rules. Multiple `allow` rules are OR'd together.
- Keep the policy minimal — only express exactly what the user described.

EXAMPLE (for a skill named "kubectl" that allows only kubectl commands):
allow {{ input.argv[0] == "kubectl" }}

EXAMPLE (for a skill that allows kubectl get and kubectl describe only):
allow {{ input.argv[0] == "kubectl"; input.argv[1] == "get" }}
allow {{ input.argv[0] == "kubectl"; input.argv[1] == "describe" }}
"""

_POLICY_HUMAN_PROMPT = """\
Skill name: {skill_name}
Skill description: {skill_description}

Policy requirement (plain English): {plain_description}

Generate the Rego rule bodies now.
"""
```

In `Planner.__init__`, after `self._summarize_chain = ...`, add:

```python
policy_prompt = ChatPromptTemplate.from_messages([
    ("system", _POLICY_SYSTEM_PROMPT),
    ("human", _POLICY_HUMAN_PROMPT),
])
self._policy_chain = policy_prompt | llm | StrOutputParser()
```

Add method to `Planner` class:

```python
def generate_policy(self, skill_name: str, skill_description: str, plain_description: str) -> str:
    return self._policy_chain.invoke({
        "skill_name": skill_name,
        "skill_description": skill_description,
        "plain_description": plain_description,
    })
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_planner.py::test_generate_policy_returns_string tests/test_planner.py::test_generate_policy_passes_all_context_to_chain -v
```
Expected: PASS

**Step 5: Run full suite**

```bash
pytest --tb=short -q
```
Expected: all tests pass (was 157 before this task)

**Step 6: Commit**

```bash
git add app/planner.py tests/test_planner.py
git commit -m "feat: add generate_policy method to Planner"
```

---

### Task 2: `POST /api/skills/generate-policy` endpoint

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_planner.py`

**Step 1: Write the failing tests**

Add to `tests/test_planner.py`:

```python
def test_api_generate_policy_returns_policy_string():
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    mock_planner = MagicMock()
    mock_planner.generate_policy.return_value = 'allow { input.argv[0] == "kubectl" }'

    with patch("app.main.Planner", return_value=mock_planner):
        client = TestClient(fastapi_app)
        res = client.post("/api/skills/generate-policy", json={
            "skill_name": "kubectl",
            "skill_description": "Kubernetes CLI",
            "description": "allow only kubectl commands",
        })

    assert res.status_code == 200
    body = res.json()
    assert "policy" in body
    assert body["policy"] == 'allow { input.argv[0] == "kubectl" }'


def test_api_generate_policy_missing_field_returns_422():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    client = TestClient(fastapi_app)
    res = client.post("/api/skills/generate-policy", json={
        "skill_name": "kubectl",
        # missing skill_description and description
    })
    assert res.status_code == 422
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_planner.py::test_api_generate_policy_returns_policy_string tests/test_planner.py::test_api_generate_policy_missing_field_returns_422 -v
```
Expected: FAIL with 404 or 405

**Step 3: Add request model and endpoint to `app/main.py`**

Near the other request models (~line 125), add:

```python
class GeneratePolicyRequest(BaseModel):
    skill_name: str
    skill_description: str
    description: str
```

**CRITICAL:** Add the endpoint BEFORE `GET /api/skills/{skill_id}`:

```python
@app.post("/api/skills/generate-policy")
def generate_policy_endpoint(body: GeneratePolicyRequest, request: Request) -> dict:
    planner = Planner(request.app.state.skill_repo)
    policy = planner.generate_policy(
        skill_name=body.skill_name,
        skill_description=body.skill_description,
        plain_description=body.description,
    )
    return {"policy": policy}
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_planner.py::test_api_generate_policy_returns_policy_string tests/test_planner.py::test_api_generate_policy_missing_field_returns_422 -v
```
Expected: PASS

**Step 5: Run full suite**

```bash
pytest --tb=short -q
```
Expected: all tests pass (was 159 after Task 1)

**Step 6: Commit**

```bash
git add app/main.py tests/test_planner.py
git commit -m "feat: add POST /api/skills/generate-policy endpoint"
```

---

### Task 3: "Generate with AI" button, inline popup, and CSS

**Files:**
- Modify: `app/static/index.html`

No automated tests for UI — visual verification only.

**Step 1: Add "Generate with AI" button in `showSkillForm`**

In `showSkillForm` (~line 1109), after building `policyInput` and before `form.appendChild(formActions)`:

```javascript
const genBtn = document.createElement('button');
genBtn.type = 'button';
genBtn.className = 'btn-sm btn-secondary';
genBtn.textContent = 'Generate with AI';
genBtn.onclick = () => showPolicyPopup(form);

form.appendChild(policyLabel);
form.appendChild(policyInput);
form.appendChild(genBtn);
form.appendChild(formActions);
```

**Step 2: Add `showPolicyPopup(form)` function**

Add after `hideSkillForm` (~line 1136):

```javascript
function showPolicyPopup(form) {
  const existing = form.querySelector('.policy-popup');
  if (existing) existing.remove();

  const popup = document.createElement('div');
  popup.className = 'policy-popup';

  const label = document.createElement('label');
  label.textContent = 'Describe the policy in plain English';

  const textarea = document.createElement('textarea');
  textarea.id = 'sf-policy-desc';
  textarea.placeholder = 'e.g. only allow kubectl get and kubectl describe commands';
  textarea.rows = 3;

  const actions = document.createElement('div');
  actions.className = 'form-actions';

  const genBtn = document.createElement('button');
  genBtn.type = 'button';
  genBtn.className = 'btn-sm btn-secondary';
  genBtn.textContent = 'Generate';
  genBtn.onclick = () => generatePolicy(popup, genBtn);

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'btn-sm btn-secondary';
  cancelBtn.textContent = 'Cancel';
  cancelBtn.onclick = () => popup.remove();

  actions.appendChild(genBtn);
  actions.appendChild(cancelBtn);
  popup.appendChild(label);
  popup.appendChild(textarea);
  popup.appendChild(actions);
  form.appendChild(popup);
  textarea.focus();
}
```

**Step 3: Add `generatePolicy(popup, genBtn)` async function**

Add after `showPolicyPopup`:

```javascript
async function generatePolicy(popup, genBtn) {
  const plainDesc = document.getElementById('sf-policy-desc').value.trim();
  if (!plainDesc) { alert('Please describe the policy first.'); return; }

  const skillName = document.getElementById('sf-name').value.trim();
  const skillDesc = document.getElementById('sf-desc').value.trim();

  genBtn.disabled = true;
  genBtn.textContent = 'Generating\u2026';

  try {
    const res = await fetch('/api/skills/generate-policy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        skill_name: skillName,
        skill_description: skillDesc,
        description: plainDesc,
      }),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();

    const policyTextarea = document.getElementById('sf-policy');
    const existing = policyTextarea.value.trim();
    policyTextarea.value = existing ? existing + '\n\n' + data.policy : data.policy;

    popup.remove();
  } catch (e) {
    alert('Failed to generate policy: ' + e.message);
    genBtn.disabled = false;
    genBtn.textContent = 'Generate';
  }
}
```

**Step 4: Add CSS for `.policy-popup`**

In the `<style>` block, after the `.skill-form` CSS block, add:

```css
.policy-popup {
  margin-top: 8px;
  padding: 10px 12px;
  background: #f1f3f4;
  border: 1px solid #dadce0;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.policy-popup label {
  font-size: 12px;
  font-weight: 500;
  color: #3c4043;
}
.policy-popup textarea {
  width: 100%;
  resize: vertical;
  font-size: 12px;
  padding: 6px 8px;
  border: 1px solid #dadce0;
  border-radius: 4px;
  background: #fff;
  box-sizing: border-box;
}
```

**Step 5: Run full suite to confirm no regressions**

```bash
pytest --tb=short -q
```
Expected: all tests pass

**Step 6: Commit**

```bash
git add app/static/index.html
git commit -m "feat: add AI policy generation button, popup, and CSS to skill form"
```
