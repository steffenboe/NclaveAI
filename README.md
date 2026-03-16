# llm-opa-agent

A FastAPI-based autonomous agent that receives a natural-language prompt (or a webhook event), plans a sequence of shell commands using an LLM, validates each command against an OPA policy before execution, and returns a structured result.

![alt text](docs/dashboard.png)

---

## Architecture

The core loop in `app/workflow.py` is unconditional code. The LLM, OPA, and subprocess are each isolated to a single call site:

```
prompt
  └── Planner.next_action()   ← LLM decides: run command / done / failed
        └── PolicyEvaluator.evaluate()  ← OPA allows or denies
              └── CommandExecutor.run()  ← subprocess executes
                    └── result appended to history → repeat
```

**Planner** (`app/planner.py`) — wraps LangChain + OpenAI-compatible LLM. Given the prompt and history, it returns the next command to run, or signals `done`/`failed`.

**PolicyEvaluator** (`app/policy.py`) — evaluates the proposed command against a Rego policy using `regopy`. The policy receives `input.argv` (the command as a list) and `input.roles` (the agent's configured roles). A command is only executed if `allow` is `true`.

**CommandExecutor** (`app/executor.py`) — runs the command via `subprocess.run` with a 30-second timeout. Returns stdout, stderr, and exit code.

All loop events are emitted as structured JSON to stdout (timestamp, run\_id, event type, relevant fields).

---

## Configuration

All settings are read from environment variables or a `.env` file (see `.env.example`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_BASE_URL` | yes | — | Base URL of the OpenAI-compatible API |
| `LLM_API_KEY` | yes | — | API key |
| `LLM_MODEL` | yes | `gpt-4.1` | Model name |
| `AGENT_ROLES` | no | `INFRA_OPERATOR` | Comma-separated roles passed to OPA as `input.roles` |
| `MAX_ITERATIONS` | no | `10` | Maximum plan→validate→execute cycles per run |
| `POLICY_PATH` | yes | — | Absolute path to the `.rego` policy file |
| `KUBECONFIG` | no | — | Path to kubeconfig (required if the agent runs kubectl) |
| `KUBE_NAMESPACE` | no | — | Namespace the agent operates in (informational, used in prompts) |

---

## OPA Policy

The agent will not execute any command unless the policy returns `allow = true`. The policy file is a standard Rego file; its path is set via `POLICY_PATH`.

**Input shape:**

```rego
input.argv   # the command as a list, e.g. ["kubectl", "get", "pods"]
input.roles  # list of role strings from AGENT_ROLES, e.g. ["INFRA_OBSERVER"]
```

**Minimal allow-all policy** (for development only):

```rego
package notesllm.agent

default allow = true
```

**Role-scoped policy** (as used in `sample-k8s-agent.yaml`):

```rego
package notesllm.agent

role_commands := {
    "INFRA_OBSERVER": [
        ["kubectl", "get"],
        ["kubectl", "logs"],
        ["kubectl", "describe"],
    ],
    "INFRA_OPERATOR": [
        ["kubectl", "get"],
        ["kubectl", "logs"],
        ["kubectl", "describe"],
        ["kubectl", "rollout"],
        ["kubectl", "scale"],
        ["helm", "upgrade"],
    ],
}

default allow = false

targets_secrets {
    input.argv[_] = "secret"
}
targets_secrets {
    input.argv[_] = "secrets"
}

observer_secrets_forbidden {
    input.roles[_] = "INFRA_OBSERVER"
    targets_secrets
}

allow {
    not observer_secrets_forbidden
    some role
    some prefix
    input.roles[_] = role
    prefix = role_commands[role][_]
    array.slice(input.argv, 0, count(prefix)) = prefix
}
```

`policies/executor.rego` in the repo is a deny-all stub. It is **not** loaded automatically; set `POLICY_PATH` to point to whichever file you want to use.

---

## Tool installation — the config-driven model

The Docker image (`python:3.12-slim` base) ships only the Python application and OS-level prerequisites (`curl`, `unzip`, `ca-certificates`, `gnupg`, `lsb-release`, `libatomic1`). It does **not** contain `az`, `kubectl`, `kubelogin`, `helm`, or any other CLI tool.

The container `CMD` is:

```
sh /etc/agent/entrypoint.sh
```

The script must be present at `/etc/agent/` at startup. How it gets there depends on the deployment method.

This means you can change which tools are installed, their versions, or the entire startup sequence by updating a config file — no image rebuild required.

### Kubernetes (recommended)

Deliver the script via the `agent-runtime` ConfigMap mounted at `/etc/agent/`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-runtime
  namespace: <your-namespace>
data:
  entrypoint.sh: |
    #!/bin/sh
    set -e
    # Install whatever tools this deployment needs
    curl -sL https://aka.ms/InstallAzureCLIDeb | bash
    curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && rm kubectl
    curl -sLo /tmp/kubelogin.zip "https://github.com/Azure/kubelogin/releases/latest/download/kubelogin-linux-amd64.zip"
    unzip /tmp/kubelogin.zip -d /tmp/kubelogin
    install -o root -g root -m 0755 /tmp/kubelogin/bin/linux_amd64/kubelogin /usr/local/bin/kubelogin
    rm -rf /tmp/kubelogin.zip /tmp/kubelogin
    # Authenticate and configure the environment, then start the app
    az login --federated-token "$(cat "$AZURE_FEDERATED_TOKEN_FILE")" \
      --service-principal --username "$AZURE_CLIENT_ID" --tenant "$AZURE_TENANT_ID" --output none
    export KUBECONFIG=/tmp/kubeconfig
    az aks get-credentials \
      --resource-group "$AKS_RESOURCE_GROUP" --name "$AKS_CLUSTER_NAME" \
      --file "$KUBECONFIG" --overwrite-existing
    kubelogin convert-kubeconfig -l workloadidentity --kubeconfig "$KUBECONFIG"
    exec uvicorn app.main:app --host 0.0.0.0 --port 8081
```

Mount it in the Deployment:

```yaml
volumeMounts:
  - name: runtime-volume        # directory mount first
    mountPath: /etc/agent
    readOnly: true
  - name: policy-volume         # subPath file overlaid on top
    mountPath: /etc/agent/policy.rego
    subPath: executor.rego
    readOnly: true

volumes:
  - name: runtime-volume
    configMap:
      name: agent-runtime
      defaultMode: 0755
  - name: policy-volume
    configMap:
      name: agent-policy
```

> **Note on mount order:** `runtime-volume` (directory) must be listed before `policy-volume` (subPath file). If the order is reversed, the directory mount shadows the file mount and `/etc/agent/policy.rego` will not be visible inside the container.

> **Note on probe delays:** Tool installation takes 60–90 seconds. Set `initialDelaySeconds: 120` on the liveness probe and `90` on the readiness probe. See `sample-k8s-agent.yaml` for the full example.

To swap tools or change the startup sequence for a given deployment, edit the ConfigMap and re-apply — no image rebuild needed:

```sh
kubectl edit configmap agent-runtime -n <namespace>
# or
kubectl apply -f updated-configmap.yaml
kubectl rollout restart deployment/agent-deployment -n <namespace>
```

### Docker (standalone)

Write `entrypoint.sh` locally, then bind-mount it:

```sh
docker run \
  -v $(pwd)/deploy/entrypoint.sh:/etc/agent/entrypoint.sh:ro \
  -v $(pwd)/policies/executor.rego:/etc/agent/policy.rego:ro \
  -e LLM_BASE_URL=https://your-llm-endpoint \
  -e LLM_API_KEY=... \
  -e LLM_MODEL=gpt-4.1 \
  -e AGENT_ROLES=INFRA_OPERATOR \
  -e POLICY_PATH=/etc/agent/policy.rego \
  -e KUBECONFIG=/tmp/kubeconfig \
  -p 8081:8081 \
  notesllm.azurecr.io/notesllm-agent:latest
```

### Host / bare-metal

If you are running the agent directly on a host (e.g. a VM or a developer machine), there is no container startup script. Install the required tools once using whatever method is appropriate for the OS, then run the app directly:

```sh
# 1. Install tools (once)
# e.g. brew install kubectl azure-cli

# 2. Configure environment
cp .env.example .env
# edit .env

# 3. Install Python dependencies
pip install -e .

# 4. Start the agent
POLICY_PATH=/path/to/policy.rego uvicorn app.main:app --host 0.0.0.0 --port 8081
```

---

## Kubernetes deployment (AKS + workload identity)

`sample-k8s-agent.yaml` contains a complete reference deployment. Resources in order:

| Resource | Name | Purpose |
|---|---|---|
| `ServiceAccount` | `notesllm-agent` | Identity for workload identity federation |
| `Role` + `RoleBinding` | `notesllm-agent-role` | Least-privilege K8s API access (get/list pods, get logs, get deployments) |
| `ConfigMap` | `agent-config` | App environment variables (LLM endpoint, model, roles, AKS details) |
| `ConfigMap` | `agent-policy` | OPA Rego policy delivered as a file |
| `ConfigMap` | `agent-runtime` | `entrypoint.sh` (see above) |
| `Deployment` | `agent-deployment` | Single replica; mounts policy and runtime ConfigMaps |
| `Service` | `agent-service` | ClusterIP on port 8081 |

**Secrets** — `LLM_API_KEY` is read from a `Secret` named `agent-secret` (key: `llm-api-key`). Create it before applying:

```sh
kubectl create secret generic agent-secret \
  --from-literal=llm-api-key=<your-key>
```

**Apply:**

```sh
kubectl apply -f sample-k8s-agent.yaml
```

---

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/agent/run` | Start a run from a natural-language prompt. Body: `{"prompt": "..."}`. Returns `{"run_id": "...", "status": "running"}` (202). |
| `GET` | `/api/agent/runs/{run_id}` | Fetch the current state of a run (history, status, final message). |
| `GET` | `/api/agent/runs` | List all runs in memory. |
| `POST` | `/api/agent/webhook` | Accept a JSON webhook payload. The agent constructs a prompt from the payload and runs autonomously. Duplicate payloads (same SHA-256 body) are deduplicated while a run is active. |
| `GET` | `/api/agent/webhooks` | List all received webhook events. |
| `GET` | `/health` | Liveness/readiness check. Returns `{"status": "ok"}`. |

A browser UI is served at `/`.

Run statuses: `running` → `done` / `failed` / `policy_denied`.

---

## Local development

```sh
# Install with dev extras
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, POLICY_PATH at minimum

# Run
uvicorn app.main:app --reload --port 8081

# Test
pytest
```

`POLICY_PATH` must point to a valid `.rego` file. For development, use the allow-all stub:

```sh
echo 'package notesllm.agent\ndefault allow = true' > /tmp/dev-policy.rego
POLICY_PATH=/tmp/dev-policy.rego uvicorn app.main:app --reload
```
