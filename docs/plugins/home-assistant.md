---
summary: "Home Assistant bridge plugin for Assist, services, speaker output, and practical Google Home voice triggers"
read_when:
  - You want Google Home or Home Assistant voice commands to reach OpenClaw
  - You need OpenClaw agents to call Home Assistant services
  - You are configuring Home Assistant speakers or Assist from OpenClaw
title: "Home Assistant plugin"
sidebarTitle: "Home Assistant"
---

The Home Assistant plugin gives OpenClaw three first-party tools:

- `home_assistant_assist` sends text to Home Assistant Assist through `/api/conversation/process`
- `home_assistant_call_service` calls an allowlisted Home Assistant service
- `home_assistant_speak` speaks or broadcasts through an allowlisted TTS or notify service

Use this as the practical Google Home bridge: Google Home triggers Home
Assistant scripts and scenes, Home Assistant calls OpenClaw through the trusted
OpenAI-compatible Gateway API, and OpenClaw can call Home Assistant back when it
needs to act or speak.

<Warning>
Google's old arbitrary "Hey Google, ask Claw ..." Conversational Actions path is
sunset. Google Home cloud-to-cloud is built around smart-home intents such as
`SYNC`, `QUERY`, `EXECUTE`, and `DISCONNECT`, not a general dictated-text
webhook. For reliable v1 setups, use fixed Google Home voice triggers exposed
through Home Assistant, or use Home Assistant Assist hardware, the HA app, or
browser Assist for natural multi-turn voice.
</Warning>

## Existing Home Assistant options

Do not build a custom Home Assistant HACS integration in this repo for v1.

Use compatible Home Assistant-side options when they fit:

- Existing OpenClaw Conversation integrations can make OpenClaw an Assist
  conversation agent inside Home Assistant.
- Home Assistant Assist can handle natural voice through HA voice hardware, the
  mobile app, or the browser.
- Home Assistant `rest_command` can call OpenClaw's `/v1/chat/completions`
  endpoint for fixed scripts and automations.

The OpenClaw plugin fills the missing OpenClaw-side surface: hardened tools for
Assist calls, allowlisted service calls, and speaker output.

## Configure

Enable the plugin under `plugins.entries.home-assistant.config`:

```json5
{
  plugins: {
    entries: {
      "home-assistant": {
        enabled: true,
        config: {
          baseUrl: "https://ha.example.com",
          accessToken: {
            source: "env",
            provider: "default",
            id: "HOME_ASSISTANT_TOKEN",
          },
          allowedServices: [
            "calendar.create_event",
            "light.turn_on",
            "notify.google_assistant_sdk",
            "tts.speak",
          ],
          defaultConversationAgentId: "home_assistant",
          defaultConversationId: "openclaw",
          defaultSpeakerTarget: {
            service: "notify.google_assistant_sdk",
            target: { entity_id: "media_player.kitchen" },
            data: { title: "OpenClaw" },
            messageField: "message",
          },
          requestTimeoutMs: 10000,
        },
      },
    },
  },
}
```

Config fields:

- `baseUrl`: Home Assistant base URL.
- `accessToken`: Home Assistant long-lived access token or SecretRef.
- `allowedServices`: exact `domain.service` allowlist. The plugin rejects all
  other service calls.
- `defaultConversationAgentId`: optional Home Assistant conversation agent id.
- `defaultConversationId`: optional conversation id for continuing Assist context.
- `defaultSpeakerTarget`: optional default speaker or notify service for
  `home_assistant_speak`.
- `requestTimeoutMs`: optional request timeout from `1` to `120000`; defaults to
  `10000`.

## Tool behavior

### `home_assistant_assist`

Posts text to Home Assistant's Conversation API:

```json
{
  "text": "Turn on the kitchen lights",
  "conversationId": "openclaw",
  "agentId": "home_assistant",
  "language": "en"
}
```

The tool returns the Home Assistant response, the returned `conversation_id`
when present, and the plain speech text when Home Assistant provides one.

### `home_assistant_call_service`

Calls one allowlisted service:

```json
{
  "service": "light.turn_on",
  "target": { "entity_id": "light.kitchen" },
  "data": { "brightness_pct": 40 }
}
```

The plugin sends this to `/api/services/light/turn_on`.

`target` is flattened into the REST service data payload because Home Assistant's
REST API receives service data directly. If a service needs its own `target`
field as service data, put that value under `data.target`.

### `home_assistant_speak`

Calls the configured or requested speaker service:

```json
{
  "text": "The calendar event is on your schedule.",
  "service": "notify.google_assistant_sdk",
  "target": { "entity_id": "media_player.kitchen" }
}
```

The selected service must be in `allowedServices`. `messageField` defaults to
`message`, which fits notify-style services. Set `messageField` and `data` when
your TTS service expects a different payload shape.

## Google Home bridge recipe

1. Expose a Home Assistant script or scene to Google Assistant.
2. Give it a fixed phrase, such as "add family event" or "ask Claw for the
   morning plan".
3. In that script, call OpenClaw's `/v1/chat/completions` Gateway endpoint with
   a trusted Gateway token.
4. Store the REST response in a Home Assistant `response_variable`.
5. Speak the returned assistant text with a Home Assistant notify or TTS service,
   or let OpenClaw call `home_assistant_speak`.

OpenClaw Gateway setup:

```json5
{
  gateway: {
    http: {
      endpoints: {
        chatCompletions: {
          enabled: true,
        },
      },
    },
  },
}
```

Home Assistant sketch:

```yaml
rest_command:
  openclaw_google_home_command:
    url: "<OPENCLAW_GATEWAY_URL>/v1/chat/completions"
    method: post
    content_type: "application/json"
    headers:
      Authorization: "Bearer <OPENCLAW_GATEWAY_TOKEN>"
      x-openclaw-session-key: "homeassistant:google-home"
      x-openclaw-message-channel: "home-assistant"
    payload: >-
      {
        "model": "openclaw",
        "messages": [
          {
            "role": "user",
            "content": "{{ prompt }}"
          }
        ]
      }

script:
  ask_claw_morning_plan:
    sequence:
      - action: rest_command.openclaw_google_home_command
        data:
          prompt: "Create the morning plan."
        response_variable: openclaw_response
      - action: notify.google_assistant_sdk
        data:
          message: "{{ openclaw_response.content.choices[0].message.content }}"
```

Treat the YAML as a starting point. Your exact speaker service and response
template depend on your Home Assistant integrations and Gateway response shape.

## Daily briefing scripts

Use this pattern when you want Google Home fixed phrases such as:

- "Hey Google, activate Claw daily briefing"
- "Hey Google, activate Claw daily briefing for Nysha"
- "Hey Google, activate Claw daily briefing for Navya"

Create one Home Assistant script per phrase and expose those scripts to Google
Assistant. The first script asks for a household briefing. The member-specific
scripts send the family member name in the prompt.

```yaml
rest_command:
  openclaw_chat:
    url: "<OPENCLAW_GATEWAY_URL>/v1/chat/completions"
    method: post
    content_type: "application/json"
    headers:
      Authorization: "Bearer <OPENCLAW_GATEWAY_TOKEN>"
      x-openclaw-session-key: "homeassistant:daily-briefing"
      x-openclaw-message-channel: "home-assistant"
    payload: >-
      {
        "model": "openclaw",
        "messages": [
          {
            "role": "system",
            "content": "You are Claw. Give a concise spoken family briefing. Mention calendar items, reminders, family tasks, and anything urgent. Keep it short enough to be spoken aloud."
          },
          {
            "role": "user",
            "content": "{{ prompt }}"
          }
        ]
      }

script:
  claw_daily_briefing:
    alias: "Claw daily briefing"
    mode: single
    sequence:
      - action: rest_command.openclaw_chat
        data:
          prompt: "Give the daily briefing for everyone in the household."
        response_variable: openclaw_response
      - action: notify.google_assistant_sdk
        data:
          message: "{{ openclaw_response.content.choices[0].message.content }}"

  claw_daily_briefing_nysha:
    alias: "Claw daily briefing for Nysha"
    mode: single
    sequence:
      - action: rest_command.openclaw_chat
        data:
          prompt: "Give the daily briefing for Nysha. Focus on her calendar, reminders, school or family tasks, and anything she needs to know today."
        response_variable: openclaw_response
      - action: notify.google_assistant_sdk
        data:
          message: "{{ openclaw_response.content.choices[0].message.content }}"

  claw_daily_briefing_navya:
    alias: "Claw daily briefing for Navya"
    mode: single
    sequence:
      - action: rest_command.openclaw_chat
        data:
          prompt: "Give the daily briefing for Navya. Focus on her calendar, reminders, school or family tasks, and anything she needs to know today."
        response_variable: openclaw_response
      - action: notify.google_assistant_sdk
        data:
          message: "{{ openclaw_response.content.choices[0].message.content }}"
```

Add one more script for each family member by copying the `claw_daily_briefing_navya`
block, changing the script id, alias, and prompt name. After Home Assistant
reloads scripts, expose each script to Google Assistant and ask Google to sync
devices.

## Security

The Home Assistant token can control every service its HA user can control. The
OpenClaw Gateway token for `/v1/chat/completions` is also full operator access.

Use these guardrails:

- Store both tokens as secrets.
- Keep the Gateway on a trusted network or behind your normal private access
  layer.
- Use a dedicated Home Assistant long-lived token with the narrowest practical
  Home Assistant user permissions.
- Keep `allowedServices` short and explicit.
- Do not expose the OpenClaw Gateway token to Google Home, public dashboards, or
  untrusted Home Assistant blueprints.

## Related docs

- [OpenAI-compatible Gateway API](/gateway/openai-http-api)
- [Gateway security](/gateway/security)
- [REST API - Home Assistant Developer Docs](https://developers.home-assistant.io/docs/api/rest/)
- [Conversation API - Home Assistant Developer Docs](https://developers.home-assistant.io/docs/intent_conversation_api/)
- [Conversational Actions sunset - Google for Developers](https://developers.google.com/assistant/ca-sunset)
- [Cloud-to-cloud intents - Google Home Developers](https://developers.home.google.com/cloud-to-cloud/primer/intents)
