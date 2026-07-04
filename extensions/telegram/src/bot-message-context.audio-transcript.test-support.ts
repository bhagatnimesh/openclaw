// Telegram plugin module implements bot message context.audio transcript support behavior.
import { beforeEach, describe, expect, it, vi } from "vitest";

const transcribeFirstAudioMock = vi.fn();
const DEFAULT_MODEL = "anthropic/claude-opus-4-5";
const DEFAULT_WORKSPACE = "/tmp/openclaw";
const DEFAULT_MENTION_PATTERN = "\\bbot\\b";

vi.mock("./media-understanding.runtime.js", () => ({
  transcribeFirstAudio: (...args: unknown[]) => transcribeFirstAudioMock(...args),
}));

const { buildTelegramMessageContextForTest } =
  await import("./bot-message-context.test-harness.js");

async function buildGroupVoiceContext(params: {
  messageId: number;
  chatId: number;
  title: string;
  date: number;
  fromId: number;
  firstName: string;
  fileId: string;
  mediaPath: string;
  groupDisableAudioPreflight?: boolean;
  topicDisableAudioPreflight?: boolean;
}) {
  const groupConfig = {
    requireMention: true,
    ...(params.groupDisableAudioPreflight === undefined
      ? {}
      : { disableAudioPreflight: params.groupDisableAudioPreflight }),
  };
  const topicConfig =
    params.topicDisableAudioPreflight === undefined
      ? undefined
      : { disableAudioPreflight: params.topicDisableAudioPreflight };

  return buildTelegramMessageContextForTest({
    message: {
      message_id: params.messageId,
      chat: { id: params.chatId, type: "supergroup", title: params.title },
      date: params.date,
      text: undefined,
      from: { id: params.fromId, first_name: params.firstName },
      voice: { file_id: params.fileId },
    },
    allMedia: [{ path: params.mediaPath, contentType: "audio/ogg" }],
    options: { forceWasMentioned: true },
    cfg: {
      agents: { defaults: { model: DEFAULT_MODEL, workspace: DEFAULT_WORKSPACE } },
      channels: { telegram: {} },
      messages: { groupChat: { mentionPatterns: [DEFAULT_MENTION_PATTERN] } },
    },
    resolveGroupActivation: () => true,
    resolveGroupRequireMention: () => true,
    resolveTelegramGroupConfig: () => ({
      groupConfig,
      topicConfig,
    }),
  });
}

function expectTranscriptRendered(
  ctx: Awaited<ReturnType<typeof buildGroupVoiceContext>>,
  transcript: string,
) {
  const framed = `[Audio transcript (machine-generated, untrusted)]: ${JSON.stringify(transcript)}`;
  expect(ctx).not.toBeNull();
  expect(ctx?.ctxPayload?.BodyForAgent).toBe(framed);
  expect(ctx?.ctxPayload?.BodyForCommands).toBe(transcript);
  expect(ctx?.ctxPayload?.CommandBody).toBe(transcript);
  expect(ctx?.ctxPayload?.RawBody).toBe(transcript);
  expect(ctx?.ctxPayload?.Transcript).toBe(transcript);
  expect(ctx?.ctxPayload?.Body).toContain(framed);
  expect(ctx?.ctxPayload?.Body).not.toContain("<media:audio>");
  expect(ctx?.ctxPayload?.MediaTranscribedIndexes).toEqual([0]);
}

function expectAudioPlaceholderRendered(ctx: Awaited<ReturnType<typeof buildGroupVoiceContext>>) {
  expect(ctx).not.toBeNull();
  expect(ctx?.ctxPayload?.Body).toContain("<media:audio>");
}

describe("buildTelegramMessageContext audio transcript body", () => {
  beforeEach(() => {
    transcribeFirstAudioMock.mockReset();
  });

  it("uses preflight transcript as BodyForAgent for mention-gated group voice messages", async () => {
    transcribeFirstAudioMock.mockResolvedValueOnce("hey bot please help");

    const ctx = await buildGroupVoiceContext({
      messageId: 1,
      chatId: -1001234567890,
      title: "Test Group",
      date: 1700000000,
      fromId: 42,
      firstName: "Alice",
      fileId: "voice-1",
      mediaPath: "/tmp/voice.ogg",
    });

    expect(transcribeFirstAudioMock).toHaveBeenCalledTimes(1);
    expectTranscriptRendered(ctx, "hey bot please help");
  });

  it("uses spoken control commands as actionable text command turns", async () => {
    transcribeFirstAudioMock.mockResolvedValueOnce("/status");

    const ctx = await buildTelegramMessageContextForTest({
      message: {
        message_id: 5,
        chat: { id: -1001234567894, type: "supergroup", title: "Test Group 5" },
        date: 1700000400,
        text: undefined,
        from: { id: 46, first_name: "Eli" },
        voice: { file_id: "voice-5" },
      },
      allMedia: [{ path: "/tmp/voice5.ogg", contentType: "audio/ogg" }],
      cfg: {
        agents: { defaults: { model: DEFAULT_MODEL, workspace: DEFAULT_WORKSPACE } },
        channels: { telegram: {} },
        commands: { useAccessGroups: false },
        messages: { groupChat: { mentionPatterns: [DEFAULT_MENTION_PATTERN] } },
      },
      resolveGroupActivation: () => true,
      resolveGroupRequireMention: () => true,
      resolveTelegramGroupConfig: () => ({
        groupConfig: { requireMention: true },
        topicConfig: undefined,
      }),
    });

    expectTranscriptRendered(ctx, "/status");
    expect(ctx?.ctxPayload?.CommandSource).toBe("text");
    expect(ctx?.ctxPayload?.CommandTurn).toMatchObject({
      authorized: true,
      body: "/status",
      commandName: "status",
      kind: "text-slash",
      source: "text",
    });
    expect(ctx?.ctxPayload?.MentionSource).toBe("command_bypass");
    expect(ctx?.ctxPayload?.WasMentioned).toBe(true);
  });

  it("skips preflight transcription when disableAudioPreflight is true", async () => {
    transcribeFirstAudioMock.mockClear();

    const ctx = await buildGroupVoiceContext({
      messageId: 2,
      chatId: -1001234567891,
      title: "Test Group 2",
      date: 1700000100,
      fromId: 43,
      firstName: "Bob",
      fileId: "voice-2",
      mediaPath: "/tmp/voice2.ogg",
      groupDisableAudioPreflight: true,
    });

    expect(transcribeFirstAudioMock).not.toHaveBeenCalled();
    expectAudioPlaceholderRendered(ctx);
  });

  it("uses topic disableAudioPreflight=false to override group disableAudioPreflight=true", async () => {
    transcribeFirstAudioMock.mockResolvedValueOnce("topic override transcript");

    const ctx = await buildGroupVoiceContext({
      messageId: 3,
      chatId: -1001234567892,
      title: "Test Group 3",
      date: 1700000200,
      fromId: 44,
      firstName: "Cara",
      fileId: "voice-3",
      mediaPath: "/tmp/voice3.ogg",
      groupDisableAudioPreflight: true,
      topicDisableAudioPreflight: false,
    });

    expect(transcribeFirstAudioMock).toHaveBeenCalledTimes(1);
    expectTranscriptRendered(ctx, "topic override transcript");
  });

  it("uses topic disableAudioPreflight=true to override group disableAudioPreflight=false", async () => {
    transcribeFirstAudioMock.mockClear();

    const ctx = await buildGroupVoiceContext({
      messageId: 4,
      chatId: -1001234567893,
      title: "Test Group 4",
      date: 1700000300,
      fromId: 45,
      firstName: "Dan",
      fileId: "voice-4",
      mediaPath: "/tmp/voice4.ogg",
      groupDisableAudioPreflight: false,
      topicDisableAudioPreflight: true,
    });

    expect(transcribeFirstAudioMock).not.toHaveBeenCalled();
    expectAudioPlaceholderRendered(ctx);
  });
});
