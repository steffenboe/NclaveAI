# llm-opa-agent

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-pytest-orange?logo=pytest)](tests/)

---

A safe-to-use, local AI agent that turns natural-language prompts into CLI command sequences — with every command gated by an [OPA](https://www.openpolicyagent.org/) policy before execution.

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Getting started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Quick start](#quick-start)
- [Skills](#skills)
- [OPA policy](#opa-policy)
- [Configuration](#configuration)
- [API reference](#api-reference)
- [Development](#development)
- [Contributing](#contributing)
- [NixOS / Nix dev shell](#nixos--nix-dev-shell)
- [License](#license)

---

## Features

- **Natural-language task execution** — describe what you want in plain English; the agent figures out the commands
- **Policy-gated execution** — every command is evaluated against a Rego policy before it runs; nothing executes without explicit permission
- **Composable skills** — teach the agent new CLI tools by writing a short description; works with `kubectl`, `gh`, `terraform`, `docker`, or anything else you have installed
- **Browser UI** — manage skills, trigger runs, and inspect execution history from a local web interface
- **OpenAI-compatible** — works with OpenAI, Azure OpenAI, Ollama, or any compatible endpoint
- **Fully local** — nothing leaves your machine except LLM API calls

---

## How it works

Each run follows a tight plan → validate → execute loop:

```
prompt
  └── Planner.next_action()       ← LLM decides: run command / done / failed
        └── PolicyEvaluator.evaluate()    ← OPA allows or denies
              └── CommandExecutor.run()   ← subprocess executes
                    └── result appended to history → repeat
```

The agent keeps iterating until the goal is reached, it hits `MAX_ITERATIONS`, or the policy blocks a required command.

---

## Getting started

### Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12+ | |
| OpenAI-compatible LLM endpoint | OpenAI, Azure OpenAI, [Ollama](https://ollama.com/), etc. |
| CLI tools you want to use | Install them yourself with `brew`, `apt`, etc. |

### Installation

```sh
# Clone the repository
git clone <repo-url>
cd llm-opa-agent

# Install the package and dev dependencies
pip install -e ".[dev]"

# Copy and fill in the environment file
cp .env.example .env
```

Open `.env` and set at minimum: `LLM_BASE_URL`, `LLM_API_KEY`, and `POLICY_PATH`.

### Quick start

```sh
# Create a permissive dev policy (⚠️ do not use in production)
echo 'package ops.agent\ndefault allow = true' > /tmp/dev-policy.rego

# Point the agent at it
echo 'POLICY_PATH=/tmp/dev-policy.rego' >> .env

# Start the server
uvicorn app.main:app --reload --port 8081
```

Open [http://localhost:8081](http://localhost:8081) in your browser.

---

## Skills

Skills tell the LLM which CLI tools are available and how to use them. Add and manage skills from the **Skills** tab in the UI.

**Example skill — `kubectl`:**

```
Name: kubectl

Description:
Kubernetes CLI. Use to inspect and manage cluster resources.

Common patterns:
- kubectl get pods -n <namespace>
- kubectl logs <pod> -n <namespace>
- kubectl describe deployment <name> -n <namespace>
- kubectl rollout restart deployment/<name> -n <namespace>

Always specify -n <namespace>. Never delete resources unless explicitly asked.
```

Once a skill is saved and enabled, the agent can use it in any run. You can enable or disable individual skills per conversation.

### Remote skill repository

Instead of managing skills manually, you can load them from a public Git repository. Configure the repository URL and branch in the **Settings modal** (open via the gear icon in the UI) under the **Remote skill repository** section. The server will clone the repo and expose all top-level `.yaml` files as read-only skills.

**Skill file format** — one `.yaml` file per skill at the root of the repository:

```yaml
name: kubectl
description: |
  Kubernetes CLI. Use to inspect and manage cluster resources.

  Common patterns:
  - kubectl get pods -n <namespace>
  - kubectl logs <pod> -n <namespace>
  - kubectl describe deployment <name> -n <namespace>
  - kubectl rollout restart deployment/<name> -n <namespace>

  Always specify -n <namespace>. Never delete resources unless explicitly asked.
enabled: true
policy: |
  allow { input.argv[0] == "kubectl" }
```

**Minimal example** (name and description are the only required fields):

```yaml
name: curl
description: |
  Use curl to make HTTP requests. Prefer -s (silent) and -f (fail on error).
  Always include -L to follow redirects.
```

**Example with a scoped policy** (only allow read-only kubectl subcommands):

```yaml
name: kubectl-readonly
description: |
  Read-only Kubernetes CLI access. Use to inspect cluster state.
  Do NOT attempt to create, delete, patch, or apply resources.
enabled: true
policy: |
  allowed := {"get", "describe", "logs", "top", "version", "cluster-info"}
  allow {
    input.argv[0] == "kubectl"
    allowed[input.argv[1]]
  }
```

**Fields:**

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | yes | — | Tool name shown in the UI and injected into the LLM prompt |
| `description` | yes | — | Full description of how the agent should use this tool |
| `enabled` | no | `true` | Whether the skill is active by default |
| `policy` | no | `null` | Rego rule bodies (no `package` line) that gate command execution |

Files in subdirectories are ignored — only top-level `.yaml` files are loaded.

**Configuration:**

Open the Skills & Settings modal → **Remote skill repository** section. Enter the repository URL and branch, then click **Save repo settings**. The server will immediately clone and sync the remote skills.

Remote skills appear with a **remote** badge in the UI and cannot be edited or deleted from the interface. Use the **Sync remote skills** button to pull the latest changes without restarting the server.

---

## OPA policy

The agent **will not execute any command** unless the policy returns `allow = true`. Point `POLICY_PATH` to a `.rego` file to control what the agent is allowed to do.

**Allow-all (development only):**

```rego
package ops.agent

default allow = true
```

**Read-only `kubectl` (production example):**

```rego
package ops.agent

default allow = false

allowed_prefixes := [
  ["kubectl", "get"],
  ["kubectl", "describe"],
  ["kubectl", "logs"],
]

allow {
  some prefix
  prefix := allowed_prefixes[_]
  array.slice(input.argv, 0, count(prefix)) == prefix
}
```

The policy receives `input.argv` — the proposed command as a list of strings. You can inspect any other property of the command from within Rego as needed.

---

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_BASE_URL` | yes | — | Base URL of the OpenAI-compatible API |
| `LLM_API_KEY` | yes | — | API key |
| `LLM_MODEL` | no | `gpt-4.1` | Model name |
| `POLICY_PATH` | yes | — | Absolute path to a `.rego` policy file |
| `MAX_ITERATIONS` | no | `10` | Maximum plan → validate → execute cycles per run |
| `SKILLS_FILE` | no | `./skills.json` | Path where skills are persisted |
| `RUNS_FILE` | no | `./runs.json` | Path where run history is persisted |
| `COMMAND_TIMEOUT_SECONDS` | no | `30` | Seconds before a running command is killed |

> **Remote skill repository** is now configured via the UI (Settings modal → Remote skill repository), not via environment variables.

---

## API reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/agent/run` | Start a run. Body: `{"prompt": "..."}` |
| `GET` | `/api/agent/runs` | List all runs |
| `GET` | `/api/agent/runs/{run_id}` | Get run status and execution history |
| `GET` | `/api/skills` | List all skills (local + remote) |
| `POST` | `/api/skills` | Create a local skill |
| `PATCH` | `/api/skills/{id}` | Update a local skill |
| `DELETE` | `/api/skills/{id}` | Delete a local skill |
| `POST` | `/api/skills/sync` | Pull latest skills from the remote Git repo |
| `GET` | `/health` | Health check |

---

## Development

### Backend

```sh
# Install Python dependencies (incl. dev extras)
pip install -e ".[dev]"

# Start the API server with auto-reload
uvicorn app.main:app --reload --port 8081
```

### Frontend

The UI lives in [`frontend/`](frontend/) and is built with [Vite](https://vite.dev/) + React.

```sh
cd frontend

# Install Node dependencies (first time only)
npm install

# Start the dev server with hot-module reload
# Proxies /api/* to the backend on :8081
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) for the dev frontend (or [http://localhost:8081](http://localhost:8081) to use the last production build served by FastAPI).

### Building the frontend for production

```sh
cd frontend && npm run build
```

This outputs the compiled assets to `app/static/`, which FastAPI serves at `/`.

### Tests

**Backend (Python):**

```sh
pytest
```

The test suite lives in [`tests/`](tests/) and uses `pytest` with `pytest-asyncio`. Contributions should include tests for any new behaviour.

**Frontend (JavaScript):**

```sh
cd frontend && npm run test
```

Frontend tests use [Vitest](https://vitest.dev/) with [Testing Library](https://testing-library.com/). They live in [`frontend/src/`](frontend/src/) alongside the components they cover.

---

## Contributing

Contributions are welcome! Here's how to get involved:

1. **Fork** the repository and create a feature branch (`git checkout -b feat/my-feature`)
2. **Make your changes** — keep the scope focused and include tests
3. **Run the test suites** (`pytest` and `cd frontend && npm run test`) and make sure everything passes
4. **Open a pull request** with a clear description of what you changed and why

For significant changes, please open an issue first to discuss the approach.

---

## NixOS / Nix dev shell

The repository ships a `flake.nix` for reproducible development on NixOS (or any system with Nix flakes enabled).

### What the flake provides

| Package | Purpose |
|---|---|
| `python312` | Python 3.12 runtime (matches `requires-python = ">=3.12"`) |
| `uv` | Python package manager — installs deps from `uv.lock` into `.venv` |
| `nodejs_22` | Node.js for the React/Vite frontend |
| `gcc.cc.lib` | GCC runtime libs — required by `regopy`'s bundled native `.so` (`libatomic.so.1`) |

On shell entry, `uv sync` automatically creates `.venv` and installs all Python dependencies.

### Enter the dev shell

```sh
nix develop
```

### Start the backend

```sh
uv run uvicorn app.main:app --reload --port 8081
```

`uvicorn` is not on `PATH` directly — use `uv run` to invoke it from the managed `.venv`.

### Notes

- **`libatomic` on Linux/NixOS** — `regopy` ships a pre-built shared library that links against `libatomic.so.1`. On Linux systems such as NixOS, the flake sets `LD_LIBRARY_PATH` to point at `gcc.cc.lib` so the dynamic linker can find it.
- **macOS** — `LD_LIBRARY_PATH` is not used in the same way on macOS, and the extra GCC runtime library may be unnecessary there.
- **Python downloads disabled** — `UV_PYTHON_DOWNLOADS=never` and `UV_PYTHON` are set in the shell hook so `uv` always uses the Nix-provided Python and never attempts to download its own.
- **`.env` file** — still required; the flake does not create it. See [Installation](#installation).

---

## License

[MIT](LICENSE) — © 2026 Exxeta AG
