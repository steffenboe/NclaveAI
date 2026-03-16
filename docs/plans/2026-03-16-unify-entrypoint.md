# Unify install-tools.sh and entrypoint.sh into a single entrypoint.sh

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Merge `install-tools.sh` and `entrypoint.sh` into a single `entrypoint.sh` script across all three places they appear: the ConfigMap, the Dockerfile CMD, and the README.

**Architecture:** The `agent-runtime` ConfigMap currently has two keys (`install-tools.sh` and `entrypoint.sh`). Both are merged into one `entrypoint.sh` key — tool installation first, then environment setup, then `exec uvicorn`. The Dockerfile CMD is simplified to just invoke `entrypoint.sh`. The README is updated to reflect the single-script model.

**Tech Stack:** Bash, Docker, Kubernetes YAML, Markdown

---

### Task 1: Merge scripts in the ConfigMap, simplify Dockerfile CMD, update README

**Files:**
- Modify: `sample-k8s-agent.yaml`
- Modify: `Dockerfile`
- Modify: `README.md`

---

**Step 1: Update the `agent-runtime` ConfigMap in `sample-k8s-agent.yaml`**

Find the `agent-runtime` ConfigMap (currently around lines 74–111). It has two data keys: `install-tools.sh` and `entrypoint.sh`.

Remove the `install-tools.sh` key entirely. Merge its contents into the top of `entrypoint.sh` (before the environment setup block), so `entrypoint.sh` becomes:

```yaml
  entrypoint.sh: |
    #!/bin/sh
    set -e

    echo "Installing Tool #1..."
    [...]

    echo "Installing Tool #2..."
    [...]

    echo "Installing Tool #3..."
    [...]

    echo "Tool installation complete."

    echo "Setting up environment..."
    [...]

    echo "Fetching AKS credentials..."
    export KUBECONFIG=/tmp/kubeconfig
    [...]

    echo "Converting kubeconfig for workload identity..."
    [...]

    # MANDATORY
    echo "Starting agent..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8081
```

The exact placeholder content (`[...]`) must be preserved exactly as it appears in the file — do not substitute real commands.

---

**Step 2: Simplify the Dockerfile CMD**

Change line 20 of `Dockerfile` from:

```dockerfile
CMD ["sh", "-c", "sh /etc/agent/install-tools.sh && exec sh /etc/agent/entrypoint.sh"]
```

to:

```dockerfile
CMD ["sh", "/etc/agent/entrypoint.sh"]
```

---

**Step 3: Update README.md**

In `README.md`, find and update every section that references `install-tools.sh` or the two-script model:

1. **Tool installation — the config-driven model** section: The `agent-runtime` ConfigMap example currently shows two keys (`install-tools.sh` and `entrypoint.sh`). Merge them into one `entrypoint.sh` key. The merged script should show the tool installation block first, then the auth/startup block. Update all prose that says "both scripts" to "the script".

2. **Dockerfile CMD** reference in the same section (the explanation of what the container `CMD` does): Update to reflect the single `exec sh /etc/agent/entrypoint.sh` invocation.

3. **Docker (standalone)** section: The `docker run` example bind-mounts two files (`install-tools.sh` and `entrypoint.sh`). Remove the `install-tools.sh` bind mount. The example should only mount `entrypoint.sh`.

4. Any other mentions of `install-tools.sh` anywhere in the file: remove or rewrite to refer to `entrypoint.sh`.

---

**Step 4: Verify YAML is still valid**

```bash
python3 -c "import yaml; docs = list(yaml.safe_load_all(open('sample-k8s-agent.yaml'))); print(f'YAML OK: {len(docs)} documents')"
```

Expected: `YAML OK: 8 documents`

---

**Step 5: Commit**

```bash
git add sample-k8s-agent.yaml Dockerfile README.md
git commit -m "refactor: merge install-tools.sh into entrypoint.sh — single script per deployment"
```
