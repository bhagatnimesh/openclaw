import { readFileSync } from "node:fs";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";
import { describe, expect, it, vi } from "vitest";
import plugin from "../index.js";
import {
  HomeAssistantConfigError,
  HomeAssistantHttpError,
  createHomeAssistantClient,
  createRequestSignal,
  normalizeHomeAssistantBaseUrl,
  resolveHomeAssistantAccessToken,
  resolveHomeAssistantConfig,
  type OpenClawCoreConfig,
} from "./home-assistant.js";

const coreConfig = {} as OpenClawCoreConfig;

describe("Home Assistant plugin manifest", () => {
  it("declares the expected tool contracts in the entrypoint and manifest", () => {
    const metadata = getToolPluginMetadata(plugin);
    const manifest = JSON.parse(
      readFileSync(new URL("../openclaw.plugin.json", import.meta.url), "utf8"),
    ) as {
      contracts?: { tools?: string[] };
      toolMetadata?: Record<string, { optional?: boolean }>;
      configSchema?: { properties?: Record<string, unknown> };
    };
    const tools = ["home_assistant_assist", "home_assistant_call_service", "home_assistant_speak"];

    expect(metadata?.id).toBe("home-assistant");
    expect(metadata?.tools.map((tool) => tool.name)).toEqual(tools);
    expect(manifest.contracts?.tools).toEqual(tools);
    expect(Object.keys(manifest.configSchema?.properties ?? {})).toEqual(
      expect.arrayContaining([
        "baseUrl",
        "accessToken",
        "allowedServices",
        "defaultConversationAgentId",
        "defaultConversationId",
        "defaultSpeakerTarget",
        "requestTimeoutMs",
      ]),
    );
    for (const tool of tools) {
      expect(manifest.toolMetadata?.[tool]?.optional).toBe(true);
    }
  });
});

describe("Home Assistant config", () => {
  it("normalizes base URLs and strips search, hashes, and trailing slashes", () => {
    expect(normalizeHomeAssistantBaseUrl(" http://homeassistant.local:8123/api/?x=1#hash ")).toBe(
      "http://homeassistant.local:8123/api",
    );
  });

  it("rejects missing, unsupported, and credentialed base URLs", () => {
    expect(() => normalizeHomeAssistantBaseUrl("")).toThrow(HomeAssistantConfigError);
    expect(() => normalizeHomeAssistantBaseUrl("file:///tmp/ha")).toThrow(HomeAssistantConfigError);
    expect(() => normalizeHomeAssistantBaseUrl("https://user:pass@ha.local")).toThrow(
      HomeAssistantConfigError,
    );
  });

  it("resolves string and SecretRef access tokens", async () => {
    await expect(
      resolveHomeAssistantAccessToken({
        pluginConfig: { accessToken: " token " },
        coreConfig,
      }),
    ).resolves.toBe("token");

    const resolveSecret = vi.fn().mockResolvedValue({ value: "resolved-token" });
    await expect(
      resolveHomeAssistantAccessToken({
        pluginConfig: {
          accessToken: {
            source: "env",
            provider: "home-assistant",
            id: "HA_TOKEN",
          },
        },
        coreConfig,
        env: {},
        resolveSecret,
      }),
    ).resolves.toBe("resolved-token");
    expect(resolveSecret).toHaveBeenCalledWith({
      config: coreConfig,
      env: {},
      value: {
        source: "env",
        provider: "home-assistant",
        id: "HA_TOKEN",
      },
      path: "plugins.entries.home-assistant.config.accessToken",
    });
  });

  it("reports missing config and bounded timeout errors", async () => {
    await expect(
      resolveHomeAssistantConfig({
        pluginConfig: { accessToken: "token" },
        coreConfig,
      }),
    ).rejects.toThrow("config.baseUrl");
    await expect(
      resolveHomeAssistantConfig({
        pluginConfig: {
          baseUrl: "http://ha.local:8123",
          accessToken: "token",
          requestTimeoutMs: 120_001,
        },
        coreConfig,
      }),
    ).rejects.toThrow("requestTimeoutMs");
  });

  it("creates a timeout signal that aborts", async () => {
    vi.useFakeTimers();
    const signal = createRequestSignal(25);
    expect(signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(25);
    expect(signal.aborted).toBe(true);
    vi.useRealTimers();
  });
});

describe("Home Assistant HTTP tools", () => {
  it("maps conversation process requests and responses", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        conversation_id: "conversation-2",
        response: {
          speech: {
            plain: {
              speech: "Done.",
            },
          },
        },
      }),
    );
    const client = await createHomeAssistantClient({
      pluginConfig: {
        baseUrl: "http://ha.local:8123/",
        accessToken: "token",
        defaultConversationAgentId: "agent-1",
        defaultConversationId: "conversation-1",
      },
      coreConfig,
      deps: { fetch: fetchMock },
    });

    await expect(
      client.processConversation({ text: "turn on the kitchen" }),
    ).resolves.toMatchObject({
      conversationId: "conversation-2",
      speech: "Done.",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://ha.local:8123/api/conversation/process",
      expect.objectContaining({
        method: "POST",
        headers: {
          Authorization: "Bearer token",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          text: "turn on the kitchen",
          conversation_id: "conversation-1",
          agent_id: "agent-1",
        }),
      }),
    );
  });

  it("allows only configured service calls", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([{ entity_id: "light.kitchen" }]));
    const client = await createHomeAssistantClient({
      pluginConfig: {
        baseUrl: "http://ha.local:8123",
        accessToken: "token",
        allowedServices: ["light.turn_on"],
      },
      coreConfig,
      deps: { fetch: fetchMock },
    });

    await expect(
      client.callService({
        service: "light.turn_on",
        data: { brightness_pct: 40 },
        target: { entity_id: "light.kitchen" },
      }),
    ).resolves.toMatchObject({ service: "light.turn_on" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://ha.local:8123/api/services/light/turn_on",
      expect.objectContaining({
        body: JSON.stringify({
          brightness_pct: 40,
          entity_id: "light.kitchen",
        }),
      }),
    );

    await expect(
      client.callService({ service: "light.turn_off", target: { entity_id: "light.kitchen" } }),
    ).rejects.toThrow("not in config.allowedServices");
  });

  it("generates speaker service payloads from defaults and overrides", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    const client = await createHomeAssistantClient({
      pluginConfig: {
        baseUrl: "http://ha.local:8123",
        accessToken: "token",
        allowedServices: ["notify.google_assistant_sdk"],
        defaultSpeakerTarget: {
          service: "notify.google_assistant_sdk",
          target: { entity_id: "media_player.kitchen" },
          data: { title: "OpenClaw" },
        },
      },
      coreConfig,
      deps: { fetch: fetchMock },
    });

    await expect(client.speak({ text: "Dinner is ready." })).resolves.toMatchObject({
      service: "notify.google_assistant_sdk",
      target: { entity_id: "media_player.kitchen" },
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://ha.local:8123/api/services/notify/google_assistant_sdk",
      expect.objectContaining({
        body: JSON.stringify({
          title: "OpenClaw",
          message: "Dinner is ready.",
          entity_id: "media_player.kitchen",
        }),
      }),
    );
  });

  it("applies request timeouts while reading the response body", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockImplementation(
      (_url: string, init: RequestInit): Promise<Response> =>
        Promise.resolve({
          ok: true,
          status: 200,
          text: () =>
            new Promise<string>((_resolve, reject) => {
              init.signal?.addEventListener("abort", () =>
                reject(new DOMException("aborted", "AbortError")),
              );
            }),
        } as Response),
    );
    const client = await createHomeAssistantClient({
      pluginConfig: {
        baseUrl: "http://ha.local:8123",
        accessToken: "token",
        requestTimeoutMs: 25,
      },
      coreConfig,
      deps: { fetch: fetchMock },
    });

    const result = expect(client.processConversation({ text: "hello" })).rejects.toThrow(
      "timed out after 25ms",
    );
    await vi.advanceTimersByTimeAsync(25);
    await result;
    vi.useRealTimers();
  });

  it("returns safe errors for Home Assistant HTTP failures", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response("unauthorized token secret-token", {
          status: 401,
          headers: { "content-type": "text/plain" },
        }),
      ),
    );
    const client = await createHomeAssistantClient({
      pluginConfig: {
        baseUrl: "http://ha.local:8123",
        accessToken: "real-token",
      },
      coreConfig,
      deps: { fetch: fetchMock },
    });

    await expect(client.processConversation({ text: "hello" })).rejects.toMatchObject({
      name: "HomeAssistantHttpError",
      status: 401,
    } satisfies Partial<HomeAssistantHttpError>);
    await expect(client.processConversation({ text: "hello" })).rejects.not.toThrow("real-token");
  });
});

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}
