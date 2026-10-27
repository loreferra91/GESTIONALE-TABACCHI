# OpenClaw / Gestionale integration

## Verified local state (2026-10-27)

- macOS app: `/Applications/OpenClaw.app`.
- CLI: `OpenClaw 2026.10.7 (157d8c2)`.
- Gateway: running as a LaunchAgent on loopback `ws://127.0.0.1:18789`, token authentication reported by `openclaw status`; Tailscale exposure is off and no channels are configured.
- Configured agents: `main`, `ios-ai`, and the new isolated `gestionale-supervisor`; no routing binding points to the Supervisor. The OpenClaw app visibly reports the same agent and its single controlled `activation-dry-run` session.
- Configuration: `openclaw config validate` passes. A pre-change backup is stored outside the repository under `~/.openclaw/backups/`.
- Supervisor policy: zero enabled skills, `minimal` tool profile, only the optional `gestionale_read` plugin tool added, `gateway` denied, elevated mode disabled, no sub-agent targets, and no agent-to-agent send targets. Its workspace contains a fail-closed read-only operating contract.
- Health: the Gateway and event loop are healthy. The deep audit probe still lacks `operator.read`; this limits the diagnostic probe, not the local Gateway's availability.
- Deep audit: one critical finding belongs to an unrelated executable onboarding skill in the `main` workspace. Additional warnings cover global cross-agent session defaults, missing plugin allowlisting, unpinned plugin install records, and absent trusted proxies. The Supervisor cannot see the flagged skill because its skill list is empty. Trusted proxies are not required while the Gateway remains loopback-only.
- CLI note: the Gateway runs the 2026.10.7 installation under the NVM path, while the first `openclaw` on the interactive shell `PATH` is an older Homebrew 2026.6.8 binary. Administrative commands for this review used the explicit 2026.10.7 binary.

Official sources used for the design:

- [OpenClaw gateway configuration](https://docs.openclaw.ai/gateway/config-gateway)
- [Tool and agent permissions](https://docs.openclaw.ai/gateway/security/tool-permissions)
- [Gateway exposure runbook](https://docs.openclaw.ai/gateway/security/exposure-runbook)
- [Prompt injection guidance](https://docs.openclaw.ai/gateway/security/prompt-injection)
- [OpenClaw 2026.10.7 release](https://github.com/openclaw/openclaw/releases)

## Architecture

```text
Operator
  -> OpenClaw.app
  -> local loopback Gateway (never public by default)
  -> GESTIONALE SUPERVISOR agent
  -> HTTPS integration API with a dedicated service identity
  -> allowlisted read-only FastAPI endpoints
  -> application-level, projected MongoDB reads
```

OpenClaw never receives MongoDB credentials, Render shell access, operator Basic Auth credentials, or a writable repository workspace. The Gestionale continues operating normally when the Gateway is offline.

## Implemented read-only API

The disabled-by-default router is mounted below `/api/agent/v1`:

- `GET /status`
- `GET /inventory`
- `GET /vending`
- `GET /orders`

Enablement requires `AGENT_INTEGRATION_ENABLED=true` and a separate random `AGENT_INTEGRATION_TOKEN` of at least 32 characters. Requests use `Authorization: Bearer …`; the operator's Basic Auth identity is deliberately not reused. Responses contain projected operational fields and aggregates, not complete MongoDB documents, secrets, product descriptions, personal data or internal configuration. All mutation methods remain unavailable.

The current rate limiter is per-process and appropriate as a first application guard. A multi-instance deployment must add a shared edge limiter before enabling the integration on more than one instance.

Only `GET` requests to the four exact paths above bypass the operator Basic Auth middleware. Unknown paths, trailing-slash variants, and non-GET methods do not. Agent responses, including failures, use `Cache-Control: no-store`; successful service authentication is logged without the credential.

## OpenClaw tool plugin

`openclaw/gestionale-supervisor-plugin` is a version-pinned OpenClaw 2026.10.7 tool plugin. It declares one optional tool, `gestionale_read`, with the closed action set `status | inventory | vending | orders`.

The plugin:

- constructs the endpoint internally and accepts no arbitrary URL or HTTP method;
- requires a bare HTTPS origin, refuses redirects, and sends one GET with the dedicated Bearer credential;
- caps timeout at 10 seconds and response size at 256 KiB;
- rejects non-JSON, malformed JSON, invalid envelopes, and upstream error bodies;
- declares the token as a capability-owned `SecretRef` input so failed secret resolution fails cold;
- has no shell, filesystem, browser, Gateway, database, deployment, messaging, or mutation surface.

Local evidence: six plugin tests pass, plugin build/validation succeeds, production-dependency audit reports zero vulnerabilities, and runtime inspection reports exactly one optional tool with no routes, hooks, services, commands, providers, or skills. The locally packed plugin is installed and allowlisted only for `gestionale-supervisor`.

An actual OpenClaw dry run invoked the tool without configuration and returned `Monitoraggio non attivo` because the credential was absent. This is the intended fail-closed state. The run used `omniroute/openclaw-resilient` with reported cost zero; the agent is pinned to that working route because the configured ACP/OpenCode model catalog was listed as available but rejected by the Gateway agent runner.

## Agent profiles

### READ ONLY (default and only implemented profile)

- Workspace access: `none`, or `ro` only for a dedicated non-secret report workspace.
- Allow only the narrow HTTP client/tool that can call the four integration endpoints.
- Deny `exec`, `process`, `write`, `edit`, `apply_patch`, `browser`, `gateway`, `cron`, `nodes`, `sessions_spawn`, and `sessions_send`.
- Set session visibility to `self`; disable agent-to-agent access.
- Do not expose the service token to prompts or logs.

### PROPOSAL (designed, not activated)

May transform verified read-only responses into proposed orders, corrections, or reports. Proposals are inert documents and have no execution endpoint. They must label `DATO VERIFICATO`, `ANOMALIA POSSIBILE`, `AZIONE CONSIGLIATA`, and `AZIONE NON AUTORIZZATA` distinctly.

### APPROVED ACTIONS (design only)

Requires a separate server-side capability model, short-lived scoped credentials, explicit operator confirmation, fingerprint-bound idempotency keys, audit records and a kill switch. Prompt text is never authorization. No endpoint or OpenClaw permission for this profile has been enabled.

## Prompt-injection boundary

CSV, Excel, descriptions, logs, API strings and web content are untrusted data. The integration API therefore exposes numeric facts and identifiers, omits free-form descriptions from anomaly feeds, and never returns instructions. OpenClaw must still run with no shell/filesystem/control-plane tools; model resistance alone is not a security boundary.

## Proposed automations (not scheduled)

- Daily: availability, negative/exhausted stock, incomplete Smart Venue confirmations, accounting indicators.
- Weekly: backup age, recurring errors, inventory anomalies and potential duplicate operations.
- Periodic technical: dependency audit, regression suite and deployment/config drift.

Each automation must be explicitly approved with schedule, timeout and notification destination. It stays silent on unchanged state, performs no writes and cannot deploy.

## Activation prerequisites

Completed: configuration validation, backups, dedicated deny-first Supervisor creation, app/CLI visibility, deep audit, application-level authorization/timeout/rate-limit regression tests, purpose-built tool implementation, package validation, local install, per-agent allowlisting, and a fail-closed live agent dry run without credentials.

Still required before activation:

1. Generate a dedicated token outside the repository, store it in Render secret configuration and an appropriate OpenClaw secret provider, and test rotation/revocation without exposing it to prompts or logs.
2. Confirm HTTPS reachability from the Mac/Gateway to Render without exposing the local Gateway.
3. Execute authorization, malformed-response, timeout, rate-limit, revocation, and Gateway-offline tests against staging or an equivalent isolated environment.
4. Obtain explicit approval before setting `AGENT_INTEGRATION_ENABLED=true` or creating any automation.

Current decision: **not activated**. Render has no Supervisor credential or enabled integration flag from this phase.
