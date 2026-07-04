// Discord tests cover agent control plugin behavior.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { maybeControlDiscordVoiceAgentRun } from "./agent-control.js";

const mocks = vi.hoisted(() => ({
  controlRealtimeVoiceAgentRun: vi.fn(),
  resolveRealtimeVoiceAgentControlIntentAsync: vi.fn(),
}));

vi.mock("openclaw/plugin-sdk/realtime-voice", () => ({
  controlRealtimeVoiceAgentRun: mocks.controlRealtimeVoiceAgentRun,
  resolveRealtimeVoiceAgentControlIntentAsync: mocks.resolveRealtimeVoiceAgentControlIntentAsync,
}));

function createEntry() {
  return { route: { sessionKey: "discord:g1:c1" } } as Parameters<
    typeof maybeControlDiscordVoiceAgentRun
  >[0]["entry"];
}

describe("maybeControlDiscordVoiceAgentRun", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.resolveRealtimeVoiceAgentControlIntentAsync.mockResolvedValue({
      mode: "cancel",
      confidence: "high",
      reason: "cancel_safety",
      shouldAutoControl: true,
    });
  });

  it("falls back for inactive cancel-like phrases", async () => {
    const result = {
      ok: true,
      active: false,
      mode: "cancel",
      sessionKey: "discord:g1:c1",
      message: "There is no active OpenClaw run to cancel.",
      speak: true,
      suppress: false,
    };
    mocks.controlRealtimeVoiceAgentRun.mockResolvedValue(result);

    await expect(
      maybeControlDiscordVoiceAgentRun({
        entry: createEntry(),
        text: "cancel my meeting tomorrow",
      }),
    ).resolves.toEqual({ handled: false, result });
    expect(mocks.controlRealtimeVoiceAgentRun).toHaveBeenCalledWith({
      sessionKey: "discord:g1:c1",
      text: "cancel my meeting tomorrow",
      mode: "cancel",
      intent: {
        mode: "cancel",
        confidence: "high",
        reason: "cancel_safety",
        shouldAutoControl: true,
      },
    });
  });

  it("handles active cancel requests", async () => {
    const result = {
      ok: true,
      active: true,
      mode: "cancel",
      sessionKey: "discord:g1:c1",
      message: "Cancelled the active OpenClaw run.",
      speak: true,
      suppress: false,
    };
    mocks.controlRealtimeVoiceAgentRun.mockResolvedValue(result);

    await expect(
      maybeControlDiscordVoiceAgentRun({
        entry: createEntry(),
        text: "cancel that",
      }),
    ).resolves.toEqual({
      handled: true,
      result,
      speakText: "Cancelled the active OpenClaw run.",
    });
  });

  it("ignores non-control phrases", async () => {
    mocks.resolveRealtimeVoiceAgentControlIntentAsync.mockResolvedValue({
      mode: "status",
      confidence: "low",
      reason: "safe_default",
      shouldAutoControl: false,
    });

    await expect(
      maybeControlDiscordVoiceAgentRun({
        entry: createEntry(),
        text: "what is next",
      }),
    ).resolves.toEqual({ handled: false });
    expect(mocks.controlRealtimeVoiceAgentRun).not.toHaveBeenCalled();
  });

  it("passes provider-enriched steering intent to active-run control", async () => {
    const classifier = vi.fn();
    const intent = {
      mode: "steer",
      confidence: "high",
      reason: "ai_classifier",
      shouldAutoControl: true,
      rawText: "usa el arreglo pequeño",
      semanticText: "Use the smaller fix.",
    };
    const result = {
      ok: true,
      active: true,
      mode: "steer",
      sessionKey: "discord:g1:c1",
      message: "Got it. I steered the active run.",
      speak: true,
      suppress: false,
    };
    mocks.resolveRealtimeVoiceAgentControlIntentAsync.mockResolvedValue(intent);
    mocks.controlRealtimeVoiceAgentRun.mockResolvedValue(result);

    await expect(
      maybeControlDiscordVoiceAgentRun({
        entry: createEntry(),
        text: "usa el arreglo pequeño",
        cfg: {} as never,
        providerConfig: { apiKey: "sk-test" },
        classifier,
      }),
    ).resolves.toEqual({
      handled: true,
      result,
      speakText: "Got it. I steered the active run.",
    });

    expect(mocks.resolveRealtimeVoiceAgentControlIntentAsync).toHaveBeenCalledWith({
      text: "usa el arreglo pequeño",
      sessionKey: "discord:g1:c1",
      cfg: {},
      providerConfig: { apiKey: "sk-test" },
      classifier,
    });
    expect(mocks.controlRealtimeVoiceAgentRun).toHaveBeenCalledWith({
      sessionKey: "discord:g1:c1",
      text: "usa el arreglo pequeño",
      mode: "steer",
      intent,
    });
  });
});
