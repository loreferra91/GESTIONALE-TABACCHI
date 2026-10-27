# OpenClaw / Gestionale integration

## Verified local state (2026-10-27)

- macOS app: `/Applications/OpenClaw.app`.
- CLI: `OpenClaw 2026.10.7 (157d8c2)`.
- Gateway: running as a LaunchAgent on loopback `ws://127.0.0.1:18789`, token authentication reported by `openclaw status`; Tailscale exposure is off and no channels are configured.
- Agents: status reports three agents and existing OpenCode-backed sessions.
- Configuration: `openclaw config validate` reports invalid legacy/current-schema fields, including the gateway token field. Because of this, plugin, skill, agent and deep-security listings did not run. No repair was attempted: `openclaw doctor --fix` would mutate user configuration and requires explicit approval plus a backup/review of the current file.
- Health: the Gateway is reachable, but the CLI does not currently have a valid read-scope credential for health RPCs.

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

1. Back up and repair the invalid OpenClaw configuration with operator approval; re-run `openclaw config validate`, `openclaw status --deep`, and `openclaw security audit --deep`.
2. Create `GESTIONALE SUPERVISOR` with the deny-first profile above and no access to the general OpenClaw/Codex/OpenCode agents.
3. Generate a dedicated token outside the repository, store it in Render secret configuration and OpenClaw's secret store, and test rotation/revocation.
4. Confirm HTTPS reachability from the Mac/Gateway to Render without exposing the local Gateway.
5. Execute the isolated authorization, timeout, rate-limit and offline tests against a staging deployment.
6. Obtain explicit approval before enabling the environment flag or creating any automation.
