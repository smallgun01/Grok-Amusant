# Configuration and safety

The sample `deploy/oci-a1/grok-staging.env.example` contains deliberately invalid image and service URL placeholders. It is a configuration reference, not a ready-to-deploy profile. Copy and edit private configuration outside version control; never place real credentials or identifying deployment details in the repository.

The example Compose configuration selects `grok_callback`, SQLite and a separate egress proxy. Relay secrets are supplied as local mounted files; the entrypoint validates file access and refuses direct secret environment values. SQLite uses a persistent bind-backed volume. The example does not publish device or callback ports and does not supply the separate device and callback ingress needed in a deployment.

A real deployment requires independently designed private device ingress, authenticated callback ingress, a securely configured proxy, owner-managed credentials, and verification of the complete routing and provider setup. Do not point an example at an actual service without an explicitly authorized deployment plan.

Firmware configuration is local to the Kai build checkout. The compile-only CI placeholders deliberately cannot serve as a real deployment configuration. See [firmware instructions](../patches/amusant-sticks3-base-v0.12.0.md).

## Outbound host policy

Set `EGRESS_PROXY_ALLOWED_HOSTS` in the private Compose environment to a comma-separated list of exact DNS names required for your deployment. Missing, empty, malformed, wildcard and IP-literal entries fail closed. CONNECT names are normalized and compared exactly; DNS results must all be public on every request. The locked `google-genai` SDK uses `generativelanguage.googleapis.com` as its default API-key Live base URL, but auxiliary endpoints and the owner-configured Grok Routine host must be verified for the actual deployment; this observation is not a complete allowlist. No real Routine URL belongs in this repository.

Both providers share one Relay process and network identity; this policy does not isolate their credentials. CONNECT hostname matching does not inspect encrypted TLS SNI and cannot distinguish other services sharing an allowed IP. Review required hosts privately and approve FAST/SLOW connectivity and rollback before rollout; an incomplete list will block traffic.
