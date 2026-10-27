# Gestionale Supervisor OpenClaw plugin

This package provides one optional tool, `gestionale_read`, for the dedicated OpenClaw `gestionale-supervisor` agent.

The tool accepts only `status`, `inventory`, `vending`, or `orders`, always performs an HTTPS `GET`, refuses redirects, limits response size and time, validates the response envelope, and never returns upstream error bodies. It has no filesystem, shell, browser, Gateway, database, deployment, or mutation capability.

Configuration is intentionally absent from the repository. `baseUrl` must be a bare HTTPS origin. `token` must be a dedicated read-only credential delivered through an OpenClaw `SecretRef`; the manifest declares it as a capability-owned secret so resolution fails cold. Do not store the token as plaintext in this repository or in prompts.

The tool is optional and is not exposed to any agent merely by installing the plugin. Activation requires an explicit per-agent allowlist and the separate server-side `AGENT_INTEGRATION_ENABLED=true` flag.
