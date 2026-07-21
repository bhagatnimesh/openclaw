import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { Type } from "typebox";
import { createHomeAssistantClient, type HomeAssistantPluginConfig } from "./src/home-assistant.js";

const secretRefSchema = Type.Object(
  {
    source: Type.Union([Type.Literal("env"), Type.Literal("file"), Type.Literal("exec")]),
    provider: Type.String({ minLength: 1 }),
    id: Type.String({ minLength: 1 }),
  },
  { additionalProperties: false },
);

const serviceIdSchema = Type.String({
  pattern: "^[a-z0-9_]+\\.[a-z0-9_]+$",
  description: "Home Assistant service id in domain.service form, such as light.turn_on.",
});

const jsonObjectSchema = Type.Record(Type.String(), Type.Unknown());

const speakerTargetSchema = Type.Object(
  {
    service: serviceIdSchema,
    target: Type.Optional(jsonObjectSchema),
    data: Type.Optional(jsonObjectSchema),
    messageField: Type.Optional(Type.String({ minLength: 1 })),
  },
  { additionalProperties: false },
);

const homeAssistantConfigSchema = Type.Object(
  {
    baseUrl: Type.Optional(
      Type.String({
        description: "Home Assistant base URL, such as http://homeassistant.local:8123.",
      }),
    ),
    accessToken: Type.Optional(
      Type.Union([Type.String({ minLength: 1 }), secretRefSchema], {
        description: "Home Assistant long-lived access token or configured OpenClaw SecretRef.",
      }),
    ),
    allowedServices: Type.Optional(
      Type.Array(serviceIdSchema, {
        description:
          "Explicit allowlist of Home Assistant services the plugin may call, such as light.turn_on.",
      }),
    ),
    defaultConversationAgentId: Type.Optional(Type.String({ minLength: 1 })),
    defaultConversationId: Type.Optional(Type.String({ minLength: 1 })),
    defaultSpeakerTarget: Type.Optional(speakerTargetSchema),
    requestTimeoutMs: Type.Optional(Type.Integer({ minimum: 1, maximum: 120_000 })),
  },
  { additionalProperties: false },
);

const assistParamsSchema = Type.Object(
  {
    text: Type.String({ minLength: 1, description: "Text to send to Home Assistant Assist." }),
    conversationId: Type.Optional(
      Type.String({
        minLength: 1,
        description: "Conversation id to continue. Defaults to plugin config if set.",
      }),
    ),
    agentId: Type.Optional(
      Type.String({
        minLength: 1,
        description: "Home Assistant conversation agent id. Defaults to plugin config if set.",
      }),
    ),
    language: Type.Optional(Type.String({ minLength: 1 })),
  },
  { additionalProperties: false },
);

const callServiceParamsSchema = Type.Object(
  {
    service: serviceIdSchema,
    data: Type.Optional(
      Type.Record(Type.String(), Type.Unknown(), {
        description: "Home Assistant service data payload.",
      }),
    ),
    target: Type.Optional(
      Type.Record(Type.String(), Type.Unknown(), {
        description: "Home Assistant service target payload.",
      }),
    ),
  },
  { additionalProperties: false },
);

const speakParamsSchema = Type.Object(
  {
    text: Type.String({ minLength: 1, description: "Text to speak or broadcast." }),
    service: Type.Optional(
      Type.String({
        pattern: "^[a-z0-9_]+\\.[a-z0-9_]+$",
        description: "Override speaker service. Defaults to config.defaultSpeakerTarget.service.",
      }),
    ),
    target: Type.Optional(
      Type.Record(Type.String(), Type.Unknown(), {
        description: "Override Home Assistant speaker target.",
      }),
    ),
    data: Type.Optional(
      Type.Record(Type.String(), Type.Unknown(), {
        description: "Additional service data for the speaker service.",
      }),
    ),
    messageField: Type.Optional(
      Type.String({
        minLength: 1,
        description: "Service data field that receives text. Defaults to message.",
      }),
    ),
  },
  { additionalProperties: false },
);

export default defineToolPlugin({
  id: "home-assistant",
  name: "Home Assistant",
  description: "Call Home Assistant Assist, services, and speaker output from OpenClaw tools.",
  configSchema: homeAssistantConfigSchema,
  tools: (tool) => [
    tool({
      name: "home_assistant_assist",
      label: "Home Assistant Assist",
      description: "Send text to Home Assistant Assist and return the response.",
      parameters: assistParamsSchema,
      optional: true,
      execute: async (params, config, context) => {
        const client = await createHomeAssistantClient({
          pluginConfig: config as HomeAssistantPluginConfig,
          coreConfig: context.api.config,
          signal: context.signal,
        });
        return await client.processConversation(params);
      },
    }),
    tool({
      name: "home_assistant_call_service",
      label: "Home Assistant Service",
      description:
        "Call an allowlisted Home Assistant service with service data and an optional target.",
      parameters: callServiceParamsSchema,
      optional: true,
      execute: async (params, config, context) => {
        const client = await createHomeAssistantClient({
          pluginConfig: config as HomeAssistantPluginConfig,
          coreConfig: context.api.config,
          signal: context.signal,
        });
        return await client.callService(params);
      },
    }),
    tool({
      name: "home_assistant_speak",
      label: "Home Assistant Speak",
      description:
        "Speak or broadcast text through an allowlisted Home Assistant speaker or TTS service.",
      parameters: speakParamsSchema,
      optional: true,
      execute: async (params, config, context) => {
        const client = await createHomeAssistantClient({
          pluginConfig: config as HomeAssistantPluginConfig,
          coreConfig: context.api.config,
          signal: context.signal,
        });
        return await client.speak(params);
      },
    }),
  ],
});
