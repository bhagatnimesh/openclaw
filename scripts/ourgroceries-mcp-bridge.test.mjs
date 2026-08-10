import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  argsForTool,
  chooseTool,
  extractAuthorizationCode,
  extractToolPayload,
  normalizeItem,
  normalizeList,
  normalizeResult,
  slugFromName,
} from "./ourgroceries-mcp-bridge.mjs";

describe("ourgroceries MCP bridge mapping", () => {
  it("chooses tools from likely OurGroceries names", () => {
    const tools = [
      { name: "ourgroceries_list_items", inputSchema: { type: "object", properties: {} } },
      { name: "ourgroceries_add_item", inputSchema: { type: "object", properties: {} } },
    ];

    assert.equal(chooseTool("list_items", tools).name, "ourgroceries_list_items");
    assert.equal(chooseTool("add_item", tools).name, "ourgroceries_add_item");
  });

  it("builds schema-shaped arguments from N4OS params", () => {
    const tool = {
      name: "addItem",
      inputSchema: {
        type: "object",
        properties: {
          listName: { type: "string" },
          itemName: { type: "string" },
          quantity: { type: "string" },
        },
      },
    };

    assert.deepEqual(
      argsForTool("add_item", tool, {
        list_slug: "whole-foods",
        item: "oat milk",
        quantity: "2",
      }),
      {
        listName: "Whole Foods",
        itemName: "oat milk",
        quantity: "2",
      },
    );
  });

  it("normalizes list names and item rows for Python provider snapshots", () => {
    assert.equal(slugFromName("WholeFoods"), "whole-foods");
    assert.deepEqual(normalizeList({ id: "list-1", name: "Indian grocery", pendingCount: 3 }), {
      slug: "indian",
      name: "Indian grocery",
      id: "list-1",
      pending_count: 3,
    });
    assert.deepEqual(
      normalizeItem(
        {
          itemId: "item-1",
          name: "paneer",
          completed: true,
          listName: "Indian grocery",
        },
        "others",
      ),
      {
        id: "item-1",
        title: "paneer",
        quantity: null,
        note: null,
        category: null,
        checked: true,
        list_slug: "indian",
      },
    );
  });

  it("extracts JSON tool payloads and normalizes list item results", () => {
    const payload = extractToolPayload({
      content: [
        {
          type: "text",
          text: JSON.stringify({ items: [{ id: "i1", title: "milk", list: "Costco" }] }),
        },
      ],
    });

    assert.deepEqual(normalizeResult("list_items", payload, { list_slug: "costco" }), [
      {
        id: "i1",
        title: "milk",
        quantity: null,
        note: null,
        category: null,
        checked: false,
        list_slug: "costco",
      },
    ]);
  });

  it("accepts a full redirected OAuth URL or a raw code", () => {
    assert.equal(
      extractAuthorizationCode("http://127.0.0.1:38441/oauth/callback?code=abc&state=s"),
      "abc",
    );
    assert.equal(extractAuthorizationCode("abc"), "abc");
  });
});
