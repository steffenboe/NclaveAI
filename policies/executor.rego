# Reference default policy — NOT loaded automatically.
# Set POLICY_PATH=/path/to/your.rego to use this file (or a custom one).
# In Kubernetes, deliver the policy via the agent-policy ConfigMap (see k8s-agent.yaml).
package ops.agent

# Default fallback policy — used when no skill policies are active.
# By default all commands are permitted; add deny rules or restrict the
# default below to harden the policy for your environment.
default allow = false


