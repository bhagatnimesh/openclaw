import { readFileSync } from "node:fs";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";
import { describe, expect, it } from "vitest";
import plugin from "./index.js";

const expectedToolNames = [
  "science_lab_profile",
  "science_lab_import_experiments",
  "science_lab_inventory",
  "science_lab_plan",
  "science_lab_experiment_status",
  "science_lab_guide_context",
  "science_lab_save_guide",
] as const;

describe("science lab plugin metadata", () => {
  it("declares the same optional tools in runtime metadata and manifest", () => {
    const metadata = getToolPluginMetadata(plugin);
    const manifest = JSON.parse(
      readFileSync(new URL("./openclaw.plugin.json", import.meta.url), "utf8"),
    ) as {
      contracts: { tools: string[] };
      toolMetadata: Record<string, { optional?: boolean }>;
    };

    expect(metadata?.id).toBe("science-lab");
    expect(metadata?.tools.map((tool) => tool.name)).toEqual([...expectedToolNames]);
    expect(manifest.contracts.tools).toEqual([...expectedToolNames]);
    for (const toolName of expectedToolNames) {
      expect(metadata?.tools.find((tool) => tool.name === toolName)?.optional).toBe(true);
      expect(manifest.toolMetadata[toolName]?.optional).toBe(true);
    }
  });
});
