#!/usr/bin/env node
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { DatabaseSync } from "node:sqlite";
import { UnauthorizedError } from "@modelcontextprotocol/sdk/client/auth.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const DEFAULT_MCP_URL = "https://www.ourgroceries.com/mcp";
const DEFAULT_REDIRECT_URL = "http://127.0.0.1:38441/oauth/callback";
const DEFAULT_DB_PATH = path.resolve("data", "n4os.db");

const LIST_NAMES = {
  indian: "Indian",
  costco: "Costco",
  "whole-foods": "Whole Foods",
  amazon: "Amazon",
  others: "Others",
};

const TOOL_CANDIDATES = {
  list_lists: [
    "list_lists",
    "listLists",
    "lists",
    "get_lists",
    "getLists",
    "ourgroceries_list_lists",
  ],
  list_items: [
    "list_items",
    "listItems",
    "get_list_items",
    "getListItems",
    "items",
    "get_items",
    "ourgroceries_list_items",
  ],
  add_item: ["add_item", "addItem", "create_item", "createItem", "add", "ourgroceries_add_item"],
  update_item: ["update_item", "updateItem", "edit_item", "editItem", "ourgroceries_update_item"],
  set_checked: [
    "set_checked",
    "setChecked",
    "check_item",
    "checkItem",
    "complete_item",
    "completeItem",
    "ourgroceries_set_checked",
  ],
  delete_item: [
    "delete_item",
    "deleteItem",
    "remove_item",
    "removeItem",
    "ourgroceries_delete_item",
  ],
  move_item: ["move_item", "moveItem", "move", "ourgroceries_move_item"],
};

class SqliteOAuthStore {
  constructor(dbPath = process.env.N4OS_DB_PATH || DEFAULT_DB_PATH) {
    this.dbPath = path.resolve(dbPath);
    fs.mkdirSync(path.dirname(this.dbPath), { recursive: true });
    this.db = new DatabaseSync(this.dbPath);
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS ourgroceries_mcp_oauth_state (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
      )
    `);
  }

  get(key) {
    const row = this.db
      .prepare("SELECT value_json FROM ourgroceries_mcp_oauth_state WHERE key = ?")
      .get(key);
    if (!row) return undefined;
    return JSON.parse(row.value_json);
  }

  set(key, value) {
    this.db
      .prepare(`
      INSERT INTO ourgroceries_mcp_oauth_state (key, value_json, updated_at)
      VALUES (?, ?, ?)
      ON CONFLICT(key) DO UPDATE SET
        value_json = excluded.value_json,
        updated_at = excluded.updated_at
    `)
      .run(key, JSON.stringify(value), new Date().toISOString());
  }

  delete(scope) {
    if (scope === "all") {
      this.db.exec("DELETE FROM ourgroceries_mcp_oauth_state");
      return;
    }
    const keysByScope = {
      client: ["clientInformation"],
      tokens: ["tokens"],
      verifier: ["codeVerifier", "oauthState", "authorizationUrl"],
      discovery: ["discoveryState"],
    };
    for (const key of keysByScope[scope] || []) {
      this.db.prepare("DELETE FROM ourgroceries_mcp_oauth_state WHERE key = ?").run(key);
    }
  }

  close() {
    this.db.close();
  }
}

class BridgeOAuthProvider {
  constructor(
    store,
    redirectUrl = process.env.N4OS_OURGROCERIES_MCP_REDIRECT_URL || DEFAULT_REDIRECT_URL,
  ) {
    this.store = store;
    this._redirectUrl = redirectUrl;
    this.clientMetadataUrl = process.env.N4OS_OURGROCERIES_MCP_CLIENT_METADATA_URL || undefined;
  }

  get redirectUrl() {
    return this._redirectUrl;
  }

  get clientMetadata() {
    return {
      client_name: process.env.N4OS_OURGROCERIES_MCP_CLIENT_NAME || "OurGroceries",
      redirect_uris: [this._redirectUrl],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
    };
  }

  state() {
    const state = crypto.randomUUID();
    this.store.set("oauthState", state);
    return state;
  }

  clientInformation() {
    return this.store.get("clientInformation");
  }

  saveClientInformation(clientInformation) {
    this.store.set("clientInformation", clientInformation);
  }

  tokens() {
    return this.store.get("tokens");
  }

  saveTokens(tokens) {
    this.store.set("tokens", tokens);
  }

  redirectToAuthorization(authorizationUrl) {
    this.store.set("authorizationUrl", String(authorizationUrl));
  }

  saveCodeVerifier(codeVerifier) {
    this.store.set("codeVerifier", codeVerifier);
  }

  codeVerifier() {
    const verifier = this.store.get("codeVerifier");
    if (!verifier) {
      throw new Error("Missing OurGroceries OAuth verifier. Run auth-url again.");
    }
    return verifier;
  }

  saveDiscoveryState(state) {
    this.store.set("discoveryState", state);
  }

  discoveryState() {
    return this.store.get("discoveryState");
  }

  invalidateCredentials(scope) {
    this.store.delete(scope);
  }
}

function mcpUrl() {
  return new URL(process.env.N4OS_OURGROCERIES_MCP_URL || DEFAULT_MCP_URL);
}

async function createConnectedClient({ allowAuthRedirect = false } = {}) {
  const store = new SqliteOAuthStore();
  const authProvider = new BridgeOAuthProvider(store);
  const transport = new StreamableHTTPClientTransport(mcpUrl(), { authProvider });
  const client = new Client(
    { name: "n4os-ourgroceries-bridge", version: "1.0.0" },
    { capabilities: {} },
  );
  try {
    await client.connect(transport);
  } catch (error) {
    const authUrl = store.get("authorizationUrl");
    if (allowAuthRedirect && error instanceof UnauthorizedError && authUrl) {
      return { client: null, transport, store, authUrl };
    }
    if (authUrl && error instanceof UnauthorizedError) {
      throw new Error(
        `OurGroceries authorization required. Run: node scripts/ourgroceries-mcp-bridge.mjs auth-url`,
      );
    }
    store.close();
    throw error;
  }
  return { client, transport, store, authUrl: null };
}

async function closeClient(resources) {
  try {
    await resources.transport?.close();
  } catch {
    // Closing a half-open MCP transport is best effort.
  }
  resources.store?.close();
}

function normalizeName(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}

function chooseTool(action, tools) {
  const candidates = new Set((TOOL_CANDIDATES[action] || []).map(normalizeName));
  let match = tools.find((tool) => candidates.has(normalizeName(tool.name)));
  if (match) return match;

  const actionTokens = normalizeName(action);
  match = tools.find((tool) => {
    const haystack = normalizeName(`${tool.name} ${tool.title || ""} ${tool.description || ""}`);
    if (action === "list_lists") return haystack.includes("list") && !haystack.includes("item");
    if (action === "list_items") return haystack.includes("item") && haystack.includes("list");
    if (action === "add_item") return haystack.includes("add") && haystack.includes("item");
    if (action === "update_item")
      return (
        (haystack.includes("update") || haystack.includes("edit")) && haystack.includes("item")
      );
    if (action === "set_checked")
      return (
        (haystack.includes("check") || haystack.includes("complete")) && haystack.includes("item")
      );
    if (action === "delete_item")
      return (
        (haystack.includes("delete") || haystack.includes("remove")) && haystack.includes("item")
      );
    if (action === "move_item") return haystack.includes("move") && haystack.includes("item");
    return haystack.includes(actionTokens);
  });
  if (match) return match;

  const names = tools.map((tool) => tool.name).join(", ");
  throw new Error(`No OurGroceries MCP tool matched action ${action}. Available tools: ${names}`);
}

function listNameFromSlug(slug) {
  return LIST_NAMES[String(slug || "")] || String(slug || "");
}

function schemaProperties(tool) {
  return Object.keys(tool?.inputSchema?.properties || {});
}

function setArg(args, key, value) {
  if (value !== undefined && value !== null && value !== "") {
    args[key] = value;
  }
}

function argsForTool(action, tool, params = {}) {
  const properties = schemaProperties(tool);
  if (properties.length === 0) {
    return fallbackArgs(action, params);
  }

  const args = {};
  for (const property of properties) {
    const key = normalizeName(property);
    if (key.includes("target") && key.includes("list")) {
      setArg(
        args,
        property,
        params.target_list_slug ? listNameFromSlug(params.target_list_slug) : undefined,
      );
    } else if (key.includes("list") && key.includes("id")) {
      setArg(args, property, params.list_id || params.list_slug);
    } else if (key.includes("list")) {
      setArg(args, property, params.list_name || listNameFromSlug(params.list_slug));
    } else if (key.includes("item") && key.includes("id")) {
      setArg(args, property, params.item_id);
    } else if (key.includes("id")) {
      setArg(args, property, params.item_id);
    } else if (key.includes("checked") || key.includes("completed") || key.includes("complete")) {
      setArg(args, property, Boolean(params.checked));
    } else if (key.includes("quantity") || key.includes("amount")) {
      setArg(args, property, params.quantity);
    } else if (key.includes("note")) {
      setArg(args, property, params.note);
    } else if (key.includes("category")) {
      setArg(args, property, params.category);
    } else if (
      key.includes("title") ||
      key.includes("name") ||
      key.includes("text") ||
      key.includes("item")
    ) {
      setArg(args, property, params.item);
    }
  }
  return args;
}

function fallbackArgs(action, params = {}) {
  if (action === "list_lists") return {};
  if (action === "list_items")
    return { list: listNameFromSlug(params.list_slug), list_slug: params.list_slug };
  if (action === "add_item") {
    return {
      list: listNameFromSlug(params.list_slug),
      list_slug: params.list_slug,
      item: params.item,
      quantity: params.quantity,
      note: params.note,
      category: params.category,
    };
  }
  if (action === "set_checked")
    return { item_id: params.item_id, checked: Boolean(params.checked) };
  if (action === "delete_item") return { item_id: params.item_id };
  if (action === "move_item") {
    return {
      item_id: params.item_id,
      target_list: listNameFromSlug(params.target_list_slug),
      target_list_slug: params.target_list_slug,
    };
  }
  return { ...params };
}

function extractToolPayload(result) {
  if (result?.structuredContent !== undefined) return result.structuredContent;
  if (result?.toolResult !== undefined) return result.toolResult;
  const textParts = (result?.content || [])
    .filter((part) => part?.type === "text" && typeof part.text === "string")
    .map((part) => part.text.trim())
    .filter(Boolean);
  if (textParts.length === 1) {
    try {
      return JSON.parse(textParts[0]);
    } catch {
      return { text: textParts[0] };
    }
  }
  if (textParts.length > 1) return { text: textParts.join("\n") };
  return result || {};
}

function normalizeList(row) {
  const slugOrName = row?.slug || row?.name || row?.title || row?.id || "";
  return {
    slug: slugFromName(slugOrName),
    name: String(row?.name || row?.title || listNameFromSlug(slugOrName) || slugOrName),
    id: row?.id,
    pending_count: row?.pending_count ?? row?.pendingCount,
  };
}

function slugFromName(value) {
  const normalized = String(value || "")
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (normalized === "wholefoods" || normalized === "whole-foods") return "whole-foods";
  if (normalized.includes("indian")) return "indian";
  if (normalized.includes("costco")) return "costco";
  if (normalized.includes("whole") && normalized.includes("food")) return "whole-foods";
  if (normalized.includes("amazon")) return "amazon";
  if (normalized === "other" || normalized === "others") return "others";
  return normalized;
}

function normalizeItem(row, fallbackListSlug) {
  const listSlug = slugFromName(
    row?.list_slug || row?.list || row?.listName || row?.list_name || fallbackListSlug,
  );
  return {
    id: String(row?.id || row?.item_id || row?.itemId || ""),
    title: String(row?.title || row?.name || row?.item || row?.text || "Untitled item"),
    quantity: row?.quantity ?? row?.amount ?? null,
    note: row?.note ?? row?.notes ?? null,
    category: row?.category ?? null,
    checked: Boolean(row?.checked || row?.completed || row?.is_checked || row?.isChecked),
    list_slug: listSlug || fallbackListSlug,
  };
}

function normalizeResult(action, payload, params = {}) {
  if (action === "list_lists") {
    const rows = Array.isArray(payload)
      ? payload
      : payload.lists || payload.shopping_lists || payload.shoppingLists || [];
    return rows.map(normalizeList);
  }
  if (action === "list_items") {
    const rows = Array.isArray(payload)
      ? payload
      : payload.items || payload.list_items || payload.listItems || [];
    return rows.map((row) => normalizeItem(row, params.list_slug));
  }
  if (action === "delete_item") {
    if (payload === null || payload === undefined) return null;
    return normalizeItem(payload.item || payload, params.list_slug);
  }
  return normalizeItem(payload.item || payload, params.list_slug || params.target_list_slug);
}

async function executeAction(action, params = {}) {
  const resources = await createConnectedClient();
  try {
    const { tools } = await resources.client.listTools();
    const tool = chooseTool(action, tools);
    const args = argsForTool(action, tool, params);
    const result = await resources.client.callTool({ name: tool.name, arguments: args });
    const payload = extractToolPayload(result);
    return {
      result: normalizeResult(action, payload, params),
      _meta: { tool: tool.name, arguments: args },
    };
  } finally {
    await closeClient(resources);
  }
}

async function authUrl() {
  const resources = await createConnectedClient({ allowAuthRedirect: true });
  try {
    if (!resources.authUrl) {
      return { status: "ok", message: "OurGroceries MCP is already authorized." };
    }
    return {
      status: "needs_authorization",
      auth_url: resources.authUrl,
      redirect_url: process.env.N4OS_OURGROCERIES_MCP_REDIRECT_URL || DEFAULT_REDIRECT_URL,
      next: "Open auth_url, sign in, copy the final redirected URL, then run auth-finish with that URL.",
    };
  } finally {
    await closeClient(resources);
  }
}

async function authFinish(value) {
  const code = extractAuthorizationCode(value);
  if (!code) {
    throw new Error("auth-finish needs an authorization code or full redirected callback URL.");
  }
  const store = new SqliteOAuthStore();
  const authProvider = new BridgeOAuthProvider(store);
  const transport = new StreamableHTTPClientTransport(mcpUrl(), { authProvider });
  try {
    await transport.finishAuth(code);
    return { status: "ok", message: "OurGroceries MCP authorization saved." };
  } finally {
    try {
      await transport.close();
    } catch {
      // Closing an auth-only transport is best effort.
    }
    store.close();
  }
}

function extractAuthorizationCode(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    return parsed.searchParams.get("code") || "";
  } catch {
    return raw;
  }
}

async function listTools() {
  const resources = await createConnectedClient();
  try {
    const result = await resources.client.listTools();
    return {
      tools: result.tools.map((tool) => ({
        name: tool.name,
        title: tool.title,
        description: tool.description,
        inputSchema: tool.inputSchema,
      })),
    };
  } finally {
    await closeClient(resources);
  }
}

async function readStdinJson() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  const body = Buffer.concat(chunks).toString("utf-8").trim();
  if (!body) throw new Error("Expected JSON request on stdin.");
  const parsed = JSON.parse(body);
  if (!parsed || typeof parsed !== "object") throw new Error("Expected a JSON object request.");
  return parsed;
}

function writeJson(payload) {
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
}

async function main(argv = process.argv.slice(2)) {
  const command = argv[0] || "call";
  if (command === "auth-url") {
    writeJson(await authUrl());
    return;
  }
  if (command === "auth-finish") {
    writeJson(await authFinish(argv.slice(1).join(" ")));
    return;
  }
  if (command === "list-tools") {
    writeJson(await listTools());
    return;
  }
  if (command === "call") {
    const request = await readStdinJson();
    const action = String(request.action || "");
    if (!action) throw new Error("Request needs an action.");
    writeJson(await executeAction(action, request.params || {}));
    return;
  }
  throw new Error(`Unknown command: ${command}`);
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((error) => {
    writeJson({
      error: error?.message || String(error),
      error_type: error?.name || "Error",
    });
    process.exitCode = 1;
  });
}

export {
  BridgeOAuthProvider,
  SqliteOAuthStore,
  argsForTool,
  chooseTool,
  extractAuthorizationCode,
  extractToolPayload,
  normalizeItem,
  normalizeList,
  normalizeResult,
  slugFromName,
};
