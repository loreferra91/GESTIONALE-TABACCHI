import { afterEach, describe, expect, it, vi } from "vitest";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";

import entry, { requestGestionale } from "./index.js";

const config = {
  baseUrl: "https://gestionale.example.test",
  token: "test-token-00000000000000000000000000000000",
  timeoutMs: 1000,
};

function envelope() {
  return {
    classification: "DATO_VERIFICATO",
    generated_at: "2026-10-27T12:00:00+00:00",
    data: { application: "available" },
    possible_anomalies: [],
    recommended_actions: [],
    unauthorized_actions: ["write_data"],
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("gestionale-supervisor metadata", () => {
  it("declares only the optional read tool", () => {
    const tools = getToolPluginMetadata(entry)?.tools;
    expect(tools?.map((tool) => tool.name)).toEqual(["gestionale_read"]);
    expect(tools?.[0]?.optional).toBe(true);
  });
});

describe("requestGestionale", () => {
  it("uses only the allowlisted GET route and bearer credential", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(envelope()), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await requestGestionale("inventory", 25, config);

    expect(result.classification).toBe("DATO_VERIFICATO");
    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(url.toString()).toBe("https://gestionale.example.test/api/agent/v1/inventory?limit=25");
    expect(init.method).toBe("GET");
    expect(init.redirect).toBe("error");
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${config.token}`);
  });

  it("fails closed without configuration", async () => {
    await expect(requestGestionale("status", undefined, {})).rejects.toThrow("credential is unavailable");
  });

  it("rejects non-HTTPS or path-bearing origins", async () => {
    await expect(requestGestionale("status", undefined, {
      ...config,
      baseUrl: "http://gestionale.example.test",
    })).rejects.toThrow("bare HTTPS origin");
    await expect(requestGestionale("status", undefined, {
      ...config,
      baseUrl: "https://gestionale.example.test/prefix",
    })).rejects.toThrow("bare HTTPS origin");
  });

  it("does not expose an upstream error body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("sensitive upstream detail", {
      status: 401,
      headers: { "content-type": "text/plain" },
    })));

    await expect(requestGestionale("status", undefined, config)).rejects.toThrow(
      "Gestionale request failed with HTTP 401",
    );
  });

  it("rejects malformed, non-JSON, and oversized responses", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("<html>login</html>", {
        status: 200,
        headers: { "content-type": "text/html" },
      }))
      .mockResolvedValueOnce(new Response("not-json", {
        status: 200,
        headers: { "content-type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response("x", {
        status: 200,
        headers: { "content-type": "application/json", "content-length": "262145" },
      }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(requestGestionale("status", undefined, config)).rejects.toThrow("non-JSON");
    await expect(requestGestionale("status", undefined, config)).rejects.toThrow("malformed JSON");
    await expect(requestGestionale("status", undefined, config)).rejects.toThrow("invalid response envelope");
    await expect(requestGestionale("status", undefined, config)).rejects.toThrow("size limit");
  });
});
