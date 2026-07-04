// Discord plugin module implements agent control behavior.
import type { OpenClawConfig } from "openclaw/plugin-sdk/config-contracts";
import {
  controlRealtimeVoiceAgentRun,
  resolveRealtimeVoiceAgentControlIntentAsync,
  type RealtimeVoiceAgentControlClassifier,
  type RealtimeVoiceAgentControlResult,
  type RealtimeVoiceProviderConfig,
} from "openclaw/plugin-sdk/realtime-voice";
import type { VoiceSessionEntry } from "./session.js";

export type DiscordVoiceAgentControlOutcome =
  | {
      handled: true;
      result: RealtimeVoiceAgentControlResult;
      speakText?: string;
    }
  | {
      handled: false;
      result?: RealtimeVoiceAgentControlResult;
    };

export async function maybeControlDiscordVoiceAgentRun(params: {
  entry: Pick<VoiceSessionEntry, "route">;
  text: string;
  cfg?: OpenClawConfig;
  providerConfig?: RealtimeVoiceProviderConfig;
  classifier?: RealtimeVoiceAgentControlClassifier;
}): Promise<DiscordVoiceAgentControlOutcome> {
  const intent = await resolveRealtimeVoiceAgentControlIntentAsync({
    text: params.text,
    sessionKey: params.entry.route.sessionKey,
    ...(params.cfg ? { cfg: params.cfg } : {}),
    ...(params.providerConfig ? { providerConfig: params.providerConfig } : {}),
    ...(params.classifier ? { classifier: params.classifier } : {}),
  });
  if (!intent.shouldAutoControl) {
    return { handled: false };
  }
  const result = await controlRealtimeVoiceAgentRun({
    sessionKey: params.entry.route.sessionKey,
    text: params.text,
    mode: intent.mode,
    intent,
  });

  if (!result.active) {
    return { handled: false, result };
  }

  return {
    handled: true,
    result,
    ...(result.speak && !result.suppress ? { speakText: result.message } : {}),
  };
}
