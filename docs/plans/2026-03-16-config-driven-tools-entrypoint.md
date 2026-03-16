# Config-Driven Tool Installation and Entrypoint Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move tool installation and the container entrypoint script out of the Docker image and into a Kubernetes ConfigMap so they can be changed per deployment without rebuilding the image.

**Architecture:** A new `agent-runtime` ConfigMap holds `install-tools.sh` (tool installation commands) and `entrypoint.sh` (startup sequence). The Dockerfile is stripped of all tool installation layers. The Deployment mounts the ConfigMap at `/etc/agent/` and the container CMD runs `install-tools.sh` then `entrypoint.sh`.

**Tech Stack:** Docker, Kubernetes (YAML), Bash

---

### Task 1: Strip tool installation from the Dockerfile

**Files:**
- Modify: `Dockerfile`

**Step 1: Remove the RUN block that installs az cli, kubectl, kubelogin**

Open `Dockerfile`. The block to remove is lines 5–20:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    ca-certificates \
    gnupg \
    lsb-release \
    libatomic1 \
    && curl -sL https://aka.ms/InstallAzureCLIDeb | bash \
    && curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && rm kubectl \
    && curl -sLo /tmp/kubelogin.zip "https://github.com/Azure/kubelogin/releases/latest/download/kubelogin-linux-amd64.zip" \
    && unzip /tmp/kubelogin.zip -d /tmp/kubelogin \
    && install -o root -g root -m 0755 /tmp/kubelogin/bin/linux_amd64/kubelogin /usr/local/bin/kubelogin \
    && rm -rf /tmp/kubelogin.zip /tmp/kubelogin \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
```

Replace it with a minimal `apt-get` that only installs the prerequisites needed by the install script at runtime (curl, unzip, ca-certificates, gnupg, lsb-release, libatomic1):

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    ca-certificates \
    gnupg \
    lsb-release \
    libatomic1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
```

**Step 2: Remove the COPY entrypoint.sh and chmod lines**

Remove these two lines from the Dockerfile:
```dockerfile
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
```

**Step 3: Update the CMD to delegate to the mounted scripts**

Change the final CMD line from:
```dockerfile
CMD ["/entrypoint.sh"]
```
to:
```dockerfile
CMD ["sh", "-c", "sh /etc/agent/install-tools.sh && exec sh /etc/agent/entrypoint.sh"]
```

**Step 4: Verify the final Dockerfile looks correct**

Expected result:
```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    ca-certificates \
    gnupg \
    lsb-release \
    libatomic1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY app/ ./app/
COPY policies/ ./policies/

CMD ["sh", "-c", "sh /etc/agent/install-tools.sh && exec sh /etc/agent/entrypoint.sh"]
```

**Step 5: Commit**

```bash
git add Dockerfile
git commit -m "feat: remove hard-wired tool installation from Dockerfile"
```

---

### Task 2: Add `agent-runtime` ConfigMap to sample-k8s-agent.yaml

**Files:**
- Modify: `sample-k8s-agent.yaml`

**Step 1: Add the new ConfigMap after the existing `agent-policy` ConfigMap (before the Deployment)**

Insert a new YAML document between the `agent-policy` ConfigMap and the Deployment section. The separator `---` before the Deployment becomes the separator after the new ConfigMap.

Add this block:

```yaml
---
# agent-runtime ConfigMap: defines tool installation and entrypoint script
apiVersion: v1
kind: ConfigMap
metadata:
  name: agent-runtime
  namespace: notesllm
data:
  install-tools.sh: |
    #!/bin/sh
    set -e

    echo "Installing Azure CLI..."
    curl -sL https://aka.ms/InstallAzureCLIDeb | bash

    echo "Installing kubectl..."
    curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
    rm kubectl

    echo "Installing kubelogin..."
    curl -sLo /tmp/kubelogin.zip "https://github.com/Azure/kubelogin/releases/latest/download/kubelogin-linux-amd64.zip"
    unzip /tmp/kubelogin.zip -d /tmp/kubelogin
    install -o root -g root -m 0755 /tmp/kubelogin/bin/linux_amd64/kubelogin /usr/local/bin/kubelogin
    rm -rf /tmp/kubelogin.zip /tmp/kubelogin

    echo "Tool installation complete."

  entrypoint.sh: |
    #!/bin/sh
    set -e

    echo "Logging in to Azure via Workload Identity..."
    az login --federated-token "$(cat "$AZURE_FEDERATED_TOKEN_FILE")" \
      --service-principal \
      --username "$AZURE_CLIENT_ID" \
      --tenant "$AZURE_TENANT_ID" \
      --output none

    echo "Fetching AKS credentials..."
    export KUBECONFIG=/tmp/kubeconfig
    az aks get-credentials \
      --resource-group "$AKS_RESOURCE_GROUP" \
      --name "$AKS_CLUSTER_NAME" \
      --overwrite-existing

    echo "Converting kubeconfig for workload identity..."
    kubelogin convert-kubeconfig -l workloadidentity --kubeconfig "$KUBECONFIG"

    echo "Starting agent..."
    exec uvicorn app.main:app --host 0.0.0.0 --port 8081
```

**Step 2: Commit**

```bash
git add sample-k8s-agent.yaml
git commit -m "feat: add agent-runtime ConfigMap with install-tools.sh and entrypoint.sh"
```

---

### Task 3: Mount `agent-runtime` ConfigMap in the Deployment

**Files:**
- Modify: `sample-k8s-agent.yaml` (Deployment section)

**Step 1: Add the volume for agent-runtime**

In the `volumes:` list of the Deployment (currently only has `policy-volume`), add:

```yaml
        - name: runtime-volume
          configMap:
            name: agent-runtime
            defaultMode: 0755
```

**Step 2: Add the volumeMount to the container**

In the container's `volumeMounts:` list (currently only has the policy mount), add:

```yaml
            - name: runtime-volume
              mountPath: /etc/agent
              readOnly: true
```

Note: `/etc/agent` now hosts both `install-tools.sh` and `entrypoint.sh`. The policy mount at `/etc/agent/policy.rego` uses `subPath` so it is unaffected — verify this is still present and correct.

**Step 3: Raise the liveness probe initialDelaySeconds**

Tool installation (especially `az cli`) takes 30–90 seconds. Update the liveness probe:

```yaml
          livenessProbe:
            httpGet:
              path: /health
              port: 8081
            initialDelaySeconds: 120
            periodSeconds: 30
```

And the readiness probe:

```yaml
          readinessProbe:
            httpGet:
              path: /health
              port: 8081
            initialDelaySeconds: 90
            periodSeconds: 10
```

**Step 4: Verify the final volumes and volumeMounts sections**

Expected `volumeMounts`:
```yaml
          volumeMounts:
            - name: runtime-volume
              mountPath: /etc/agent
              readOnly: true
            - name: policy-volume
              mountPath: /etc/agent/policy.rego
              subPath: executor.rego
              readOnly: true
```

Expected `volumes`:
```yaml
      volumes:
        - name: policy-volume
          configMap:
            name: agent-policy
        - name: runtime-volume
          configMap:
            name: agent-runtime
            defaultMode: 0755
```

**Step 5: Commit**

```bash
git add sample-k8s-agent.yaml
git commit -m "feat: mount agent-runtime ConfigMap into Deployment; raise probe delays for tool install time"
```

---

### Task 4: Verify the YAML is valid

**Step 1: Lint the K8s YAML**

Run:
```bash
kubectl apply --dry-run=client -f sample-k8s-agent.yaml
```
Expected: each resource prints `(dry run)` with no errors.

If `kubectl` is not available locally, use:
```bash
python3 -c "import yaml; list(yaml.safe_load_all(open('sample-k8s-agent.yaml')))" && echo "YAML parse OK"
```

**Step 2: Verify the Dockerfile builds (optional but recommended)**

```bash
docker build -t llm-opa-agent:test .
```
Expected: build succeeds; no tool binaries baked in.

**Step 3: Commit if any minor fixes were needed**

```bash
git add .
git commit -m "fix: yaml/dockerfile corrections after dry-run validation"
```
