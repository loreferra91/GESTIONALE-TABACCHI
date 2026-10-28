import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";

const ACTION_PATHS = {
  status: "/api/agent/v1/status",
  inventory: "/api/agent/v1/inventory",
  vending: "/api/agent/v1/vending",
  orders: "/api/agent/v1/orders",
} as const;

type Action = keyof typeof ACTION_PATHS;
type SecretRef = {
  source: "env" | "file" | "exec" | "store";
  provider: string;
  id: string;
};
type PluginConfig = {
  baseUrl?: string;
  token?: string | SecretRef;
  timeoutMs?: number;
};

const MAX_RESPONSE_BYTES = 256 * 1024;

function requireConfig(config: PluginConfig) {
  const baseUrl = String(config.baseUrl ?? "").trim();
  // OpenClaw materializes supported SecretRefs before activating this plugin.
  // Refuse an unresolved object instead of coercing it into a fake credential.
  if (config.token && typeof config.token === "object") {
    throw new Error("Gestionale Supervisor credential reference was not resolved");
  }
  const token = typeof config.token === "string" ? config.token : "";
  const timeoutMs = config.timeoutMs ?? 4000;

  if (token.length < 32) {
    throw new Error("Gestionale Supervisor credential is unavailable");
  }
  if (!Number.isInteger(timeoutMs) || timeoutMs < 250 || timeoutMs > 10000) {
    throw new Error("Gestionale Supervisor timeout configuration is invalid");
  }

  let origin: URL;
  try {
    origin = new URL(baseUrl);
  } catch {
    throw new Error("Gestionale Supervisor base URL is invalid");
  }
  if (
    origin.protocol !== "https:" ||
    origin.username ||
    origin.password ||
    origin.search ||
    origin.hash ||
    origin.pathname !== "/"
  ) {
    throw new Error("Gestionale Supervisor requires a bare HTTPS origin");
  }

  return { origin, token, timeoutMs };
}

async function readBoundedText(response: Response): Promise<string> {
  const contentLength = Number(response.headers.get("content-length") ?? "0");
  if (Number.isFinite(contentLength) && contentLength > MAX_RESPONSE_BYTES) {
    throw new Error("Gestionale response exceeded the size limit");
  }
  if (!response.body) {
    return "";
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    received += value.byteLength;
    if (received > MAX_RESPONSE_BYTES) {
      await reader.cancel();
      throw new Error("Gestionale response exceeded the size limit");
    }
    chunks.push(value);
  }

  const merged = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(merged);
}

function validateEnvelope(value: unknown): asserts value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Gestionale returned an invalid response envelope");
  }
  const envelope = value as Record<string, unknown>;
  if (
    envelope.classification !== "DATO_VERIFICATO" ||
    typeof envelope.generated_at !== "string" ||
    !envelope.data ||
    typeof envelope.data !== "object" ||
    Array.isArray(envelope.data) ||
    !Array.isArray(envelope.possible_anomalies) ||
    !Array.isArray(envelope.recommended_actions) ||
    !Array.isArray(envelope.unauthorized_actions)
  ) {
    throw new Error("Gestionale returned an invalid response envelope");
  }
}

export async function requestGestionale(
  action: Action,
  limit: number | undefined,
  config: PluginConfig,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  const { origin, token, timeoutMs } = requireConfig(config);
  const url = new URL(ACTION_PATHS[action], origin);
  if (action !== "status" && limit !== undefined) {
    url.searchParams.set("limit", String(limit));
  }

  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const requestSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;
  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
      },
      redirect: "error",
      signal: requestSignal,
    });
  } catch (error) {
    if (requestSignal.aborted) {
      throw new Error("Gestionale request timed out or was cancelled");
    }
    throw new Error("Gestionale request failed", { cause: error });
  }

  if (!response.ok) {
    throw new Error(`Gestionale request failed with HTTP ${response.status}`);
  }
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.includes("application/json")) {
    throw new Error("Gestionale returned a non-JSON response");
  }

  const text = await readBoundedText(response);
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("Gestionale returned malformed JSON");
  }
  validateEnvelope(value);
  return value;
}

export default defineToolPlugin({
  id: "gestionale-supervisor",
  name: "Gestionale Supervisor",
  description: "Read-only access to the allowlisted Gestionale supervision API.",
  configSchema: Type.Object(
    {
      baseUrl: Type.Optional(Type.String({ minLength: 1 })),
      token: Type.Optional(Type.Union([
        Type.String({ minLength: 32 }),
        Type.Object(
          {
            source: Type.Union([
              Type.Literal("env"),
              Type.Literal("file"),
              Type.Literal("exec"),
              Type.Literal("store"),
            ]),
            provider: Type.String({ minLength: 1 }),
            id: Type.String({ minLength: 1 }),
          },
          { additionalProperties: false },
        ),
      ])),
      timeoutMs: Type.Optional(Type.Integer({ minimum: 250, maximum: 10000, default: 4000 })),
    },
    { additionalProperties: false },
  ),
  tools: (tool) => [
    tool({
      name: "gestionale_read",
      label: "Gestionale Read",
      description: "Read one allowlisted Gestionale supervision view. Never modifies data.",
      optional: true,
      parameters: Type.Object(
        {
          action: Type.Union([
            Type.Literal("status"),
            Type.Literal("inventory"),
            Type.Literal("vending"),
            Type.Literal("orders"),
          ]),
          limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 500 })),
        },
        { additionalProperties: false },
      ),
      async execute({ action, limit }, config, context) {
        context.signal?.throwIfAborted();
        return requestGestionale(action, limit, config, context.signal);
      },
    }),
  ],
});
