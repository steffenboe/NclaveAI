# Reference default policy — NOT loaded automatically.
# Set POLICY_PATH=/path/to/your.rego to use this file (or a custom one).
# In Kubernetes, deliver the policy via the agent-policy ConfigMap (see k8s-agent.yaml).
package ops.agent

default allow = false


