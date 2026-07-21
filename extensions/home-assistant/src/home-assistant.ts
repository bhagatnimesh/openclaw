import { resolveConfiguredSecretInputString } from "openclaw/plugin-sdk/secret-input-runtime";

export const HOME_ASSISTANT_DEFAULT_TIMEOUT_MS = 10_000;
export const HOME_ASSISTANT_MAX_TIMEOUT_MS = 120_000;

const SERVICE_ID_PATTERN = /^[a-z0-9_]+\.[a-z0-9_]+$/;

export type SecretRefInput = {
  source: "env" | "file" | "exec";
  provider: string;
  id: string;
};

export type SecretInput = string | SecretRefInput;
export type OpenClawCoreConfig = Parameters<typeof resolveConfiguredSecretInputString>[0]["config"];

export type HomeAssistantSpeakerTarget = {
  service: string;
  target?: Record<string, unknown>;
  data?: Record<string, unknown>;
  messageField?: string;
};

export type HomeAssistantPluginConfig = {
  baseUrl?: string;
  accessToken?: SecretInput;
  allowedServices?: string[];
  defaultConversationAgentId?: string;
  defaultConversationId?: string;
  defaultSpeakerTarget?: HomeAssistantSpeakerTarget;
  requestTimeoutMs?: number;
};

export type ResolvedHomeAssistantConfig = {
  baseUrl: string;
  accessToken: string;
  allowedServices: ReadonlySet<string>;
  defaultConversationAgentId?: string;
  defaultConversationId?: string;
  defaultSpeakerTarget?: HomeAssistantSpeakerTarget;
  requestTimeoutMs: number;
};

export type HomeAssistantAssistParams = {
  text: string;
  conversationId?: string;
  agentId?: string;
  language?: string;
};

export type HomeAssistantCallServiceParams = {
  service: string;
  data?: Record<string, unknown>;
  target?: Record<string, unknown>;
};

export type HomeAssistantSpeakParams = {
  text: string;
  service?: string;
  target?: Record<string, unknown>;
  data?: Record<string, unknown>;
  messageField?: string;
};

export type HomeAssistantFetch = typeof fetch;

export type SecretResolver = (params: {
  config: OpenClawCoreConfig;
  env: NodeJS.ProcessEnv;
  value: SecretInput;
  path: string;
}) => Promise<{ value?: string; unresolvedRefReason?: string }>;

export type HomeAssistantClientDeps = {
  fetch?: HomeAssistantFetch;
  env?: NodeJS.ProcessEnv;
  resolveSecret?: SecretResolver;
};

export class HomeAssistantConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "HomeAssistantConfigError";
  }
}

export class HomeAssistantHttpError extends Error {
  readonly status: number;
  readonly safeBody?: string;

  constructor(status: number, safeBody?: string) {
    super(`Home Assistant request failed with HTTP ${status}${safeBody ? `: ${safeBody}` : ""}`);
    this.name = "HomeAssistantHttpError";
    this.status = status;
    this.safeBody = safeBody;
  }
}

export function normalizeHomeAssistantBaseUrl(value: unknown): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new HomeAssistantConfigError("Home Assistant plugin requires config.baseUrl.");
  }
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    throw new HomeAssistantConfigError("Home Assistant config.baseUrl must be a valid URL.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new HomeAssistantConfigError("Home Assistant config.baseUrl must use http or https.");
  }
  if (url.username || url.password) {
    throw new HomeAssistantConfigError(
      "Home Assistant config.baseUrl must not include credentials.",
    );
  }
  url.pathname = url.pathname.replace(/\/+$/u, "");
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/u, "");
}

export function normalizeServiceId(service: unknown): string {
  if (typeof service !== "string" || !SERVICE_ID_PATTERN.test(service)) {
    throw new HomeAssistantConfigError(
      "Home Assistant service must be in domain.service form with lowercase letters, numbers, or underscores.",
    );
  }
  return service;
}

export function splitServiceId(service: string): { domain: string; service: string } {
  const [domain, serviceName] = normalizeServiceId(service).split(".");
  if (!domain || !serviceName) {
    throw new HomeAssistantConfigError(`Invalid Home Assistant service: ${service}`);
  }
  return { domain, service: serviceName };
}

export async function resolveHomeAssistantAccessToken(params: {
  pluginConfig: HomeAssistantPluginConfig;
  coreConfig: OpenClawCoreConfig;
  env?: NodeJS.ProcessEnv;
  resolveSecret?: SecretResolver;
}): Promise<string> {
  const { accessToken } = params.pluginConfig;
  if (typeof accessToken === "string" && accessToken.trim() !== "") {
    return accessToken.trim();
  }
  if (!accessToken || typeof accessToken !== "object") {
    throw new HomeAssistantConfigError("Home Assistant plugin requires config.accessToken.");
  }
  const resolveSecret = params.resolveSecret ?? resolveConfiguredSecretInputString;
  const resolved = await resolveSecret({
    config: params.coreConfig,
    env: params.env ?? process.env,
    value: accessToken,
    path: "plugins.entries.home-assistant.config.accessToken",
  });
  if (resolved.value && resolved.value.trim() !== "") {
    return resolved.value.trim();
  }
  throw new HomeAssistantConfigError(
    resolved.unresolvedRefReason ??
      "Home Assistant config.accessToken SecretRef could not be resolved.",
  );
}

export async function resolveHomeAssistantConfig(params: {
  pluginConfig: HomeAssistantPluginConfig;
  coreConfig: OpenClawCoreConfig;
  env?: NodeJS.ProcessEnv;
  resolveSecret?: SecretResolver;
}): Promise<ResolvedHomeAssistantConfig> {
  const baseUrl = normalizeHomeAssistantBaseUrl(params.pluginConfig.baseUrl);
  const accessToken = await resolveHomeAssistantAccessToken(params);
  const allowedServices = new Set(
    (params.pluginConfig.allowedServices ?? []).map((service) => normalizeServiceId(service)),
  );
  return {
    baseUrl,
    accessToken,
    allowedServices,
    defaultConversationAgentId: trimOptional(params.pluginConfig.defaultConversationAgentId),
    defaultConversationId: trimOptional(params.pluginConfig.defaultConversationId),
    defaultSpeakerTarget: normalizeSpeakerTarget(params.pluginConfig.defaultSpeakerTarget),
    requestTimeoutMs: normalizeTimeoutMs(params.pluginConfig.requestTimeoutMs),
  };
}

export function assertServiceAllowed(service: string, allowedServices: ReadonlySet<string>): void {
  const normalizedService = normalizeServiceId(service);
  if (!allowedServices.has(normalizedService)) {
    throw new HomeAssistantConfigError(
      `Home Assistant service ${normalizedService} is not in config.allowedServices.`,
    );
  }
}

export async function createHomeAssistantClient(params: {
  pluginConfig: HomeAssistantPluginConfig;
  coreConfig: OpenClawCoreConfig;
  signal?: AbortSignal;
  deps?: HomeAssistantClientDeps;
}): Promise<HomeAssistantClient> {
  const config = await resolveHomeAssistantConfig({
    pluginConfig: params.pluginConfig,
    coreConfig: params.coreConfig,
    env: params.deps?.env,
    resolveSecret: params.deps?.resolveSecret,
  });
  return new HomeAssistantClient({
    config,
    fetch: params.deps?.fetch ?? fetch,
    signal: params.signal,
  });
}

export class HomeAssistantClient {
  private readonly config: ResolvedHomeAssistantConfig;
  private readonly fetchImpl: HomeAssistantFetch;
  private readonly parentSignal?: AbortSignal;

  constructor(params: {
    config: ResolvedHomeAssistantConfig;
    fetch: HomeAssistantFetch;
    signal?: AbortSignal;
  }) {
    this.config = params.config;
    this.fetchImpl = params.fetch;
    this.parentSignal = params.signal;
  }

  async processConversation(params: HomeAssistantAssistParams): Promise<{
    conversationId?: string;
    speech?: string;
    response: unknown;
  }> {
    const body: Record<string, unknown> = {
      text: params.text,
    };
    const conversationId = trimOptional(params.conversationId) ?? this.config.defaultConversationId;
    const agentId = trimOptional(params.agentId) ?? this.config.defaultConversationAgentId;
    const language = trimOptional(params.language);
    if (conversationId) {
      body.conversation_id = conversationId;
    }
    if (agentId) {
      body.agent_id = agentId;
    }
    if (language) {
      body.language = language;
    }
    const response = await this.request("/api/conversation/process", body);
    return {
      conversationId: readStringProperty(response, "conversation_id"),
      speech: extractConversationSpeech(response),
      response,
    };
  }

  async callService(params: HomeAssistantCallServiceParams): Promise<{
    service: string;
    response: unknown;
  }> {
    const service = normalizeServiceId(params.service);
    assertServiceAllowed(service, this.config.allowedServices);
    const { domain, service: serviceName } = splitServiceId(service);
    const body = buildServicePayload(params.data, params.target);
    const response = await this.request(`/api/services/${domain}/${serviceName}`, body);
    return { service, response };
  }

  async speak(params: HomeAssistantSpeakParams): Promise<{
    service: string;
    target?: Record<string, unknown>;
    response: unknown;
  }> {
    const defaultTarget = this.config.defaultSpeakerTarget;
    const service = normalizeServiceId(params.service ?? defaultTarget?.service);
    assertServiceAllowed(service, this.config.allowedServices);
    const messageField =
      trimOptional(params.messageField) ?? defaultTarget?.messageField ?? "message";
    const data = {
      ...(defaultTarget?.data ?? {}),
      ...(params.data ?? {}),
      [messageField]: params.text,
    };
    const target = params.target ?? defaultTarget?.target;
    const { domain, service: serviceName } = splitServiceId(service);
    const response = await this.request(
      `/api/services/${domain}/${serviceName}`,
      buildServicePayload(data, target),
    );
    return { service, ...(target ? { target } : {}), response };
  }

  private async request(path: string, body: Record<string, unknown>): Promise<unknown> {
    const requestSignal = createManagedRequestSignal(
      this.config.requestTimeoutMs,
      this.parentSignal,
    );
    try {
      const response = await this.fetchImpl(`${this.config.baseUrl}${path}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.config.accessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: requestSignal.signal,
      });
      if (!response.ok) {
        throw new HomeAssistantHttpError(response.status);
      }
      return await readJsonResponse(response);
    } catch (error) {
      if (isAbortError(error)) {
        throw new HomeAssistantConfigError(
          `Home Assistant request timed out after ${this.config.requestTimeoutMs}ms.`,
        );
      }
      throw error;
    } finally {
      requestSignal.clear();
    }
  }
}

export function createRequestSignal(timeoutMs: number, parentSignal?: AbortSignal): AbortSignal {
  return createManagedRequestSignal(timeoutMs, parentSignal).signal;
}

function createManagedRequestSignal(
  timeoutMs: number,
  parentSignal?: AbortSignal,
): { signal: AbortSignal; clear: () => void } {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const clear = (): void => clearTimeout(timeout);
  controller.signal.addEventListener("abort", clear, { once: true });
  if (parentSignal) {
    if (parentSignal.aborted) {
      controller.abort();
    } else {
      parentSignal.addEventListener("abort", () => controller.abort(), { once: true });
    }
  }
  return { signal: controller.signal, clear };
}

function normalizeTimeoutMs(value: unknown): number {
  if (value === undefined) {
    return HOME_ASSISTANT_DEFAULT_TIMEOUT_MS;
  }
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < 1 ||
    value > HOME_ASSISTANT_MAX_TIMEOUT_MS
  ) {
    throw new HomeAssistantConfigError(
      `Home Assistant requestTimeoutMs must be an integer from 1 to ${HOME_ASSISTANT_MAX_TIMEOUT_MS}.`,
    );
  }
  return value;
}

function normalizeSpeakerTarget(
  target: HomeAssistantSpeakerTarget | undefined,
): HomeAssistantSpeakerTarget | undefined {
  if (!target) {
    return undefined;
  }
  return {
    service: normalizeServiceId(target.service),
    ...(target.target ? { target: target.target } : {}),
    ...(target.data ? { data: target.data } : {}),
    ...(trimOptional(target.messageField)
      ? { messageField: trimOptional(target.messageField) }
      : {}),
  };
}

function trimOptional(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : undefined;
}

function buildServicePayload(
  data: Record<string, unknown> | undefined,
  target: Record<string, unknown> | undefined,
): Record<string, unknown> {
  return {
    ...(data ?? {}),
    ...(target ?? {}),
  };
}

function extractConversationSpeech(response: unknown): string | undefined {
  const root = asRecord(response);
  const responseRecord = asRecord(root?.response);
  const speech = asRecord(responseRecord?.speech);
  const plain = asRecord(speech?.plain);
  return readStringProperty(plain, "speech");
}

function readStringProperty(value: unknown, key: string): string | undefined {
  const record = asRecord(value);
  const property = record?.[key];
  return typeof property === "string" && property !== "" ? property : undefined;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

async function readJsonResponse(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text.trim() === "") {
    return null;
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new HomeAssistantHttpError(response.status, "Invalid JSON response from Home Assistant.");
  }
}

function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError") {
    return true;
  }
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: unknown }).name === "AbortError"
  );
}
