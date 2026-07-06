import type {
  ExperimentProgress,
  ExperimentRecord,
  InventoryEntry,
  OpenScienceLabStateStore,
  SavedGuideRecord,
  ScienceLabProfile,
} from "./types.js";

const PROFILE_KEY = "current";

const STORE_LIMITS = {
  profiles: 4,
  experiments: 2_000,
  inventory: 5_000,
  progress: 5_000,
  guides: 1_000,
};

export function createScienceLabState(openKeyedStore: OpenScienceLabStateStore) {
  const profiles = openKeyedStore<ScienceLabProfile>({
    namespace: "profiles",
    maxEntries: STORE_LIMITS.profiles,
  });
  const experiments = openKeyedStore<ExperimentRecord>({
    namespace: "experiments",
    maxEntries: STORE_LIMITS.experiments,
  });
  const inventory = openKeyedStore<InventoryEntry>({
    namespace: "inventory",
    maxEntries: STORE_LIMITS.inventory,
  });
  const progress = openKeyedStore<ExperimentProgress>({
    namespace: "progress",
    maxEntries: STORE_LIMITS.progress,
  });
  const guides = openKeyedStore<SavedGuideRecord>({
    namespace: "guides",
    maxEntries: STORE_LIMITS.guides,
  });

  return {
    async getProfile(): Promise<ScienceLabProfile | undefined> {
      return profiles.lookup(PROFILE_KEY);
    },

    async saveProfile(profile: ScienceLabProfile): Promise<void> {
      await profiles.register(PROFILE_KEY, profile);
    },

    async upsertExperiments(records: readonly ExperimentRecord[]): Promise<void> {
      for (const record of records) {
        await experiments.register(record.id, record);
      }
    },

    async getExperiment(id: string): Promise<ExperimentRecord | undefined> {
      return experiments.lookup(id);
    },

    async listExperiments(): Promise<ExperimentRecord[]> {
      const entries = await experiments.entries();
      return entries.map((entry) => entry.value).sort(compareExperiments);
    },

    async upsertInventory(entries: readonly InventoryEntry[]): Promise<void> {
      for (const entry of entries) {
        await inventory.register(entry.materialId, entry);
      }
    },

    async listInventory(): Promise<InventoryEntry[]> {
      const entries = await inventory.entries();
      return entries
        .map((entry) => entry.value)
        .sort((a, b) => a.displayName.localeCompare(b.displayName));
    },

    async getProgress(experimentId: string): Promise<ExperimentProgress | undefined> {
      return progress.lookup(experimentId);
    },

    async upsertProgress(record: ExperimentProgress): Promise<void> {
      await progress.register(record.experimentId, record);
    },

    async listProgress(): Promise<ExperimentProgress[]> {
      const entries = await progress.entries();
      return entries.map((entry) => entry.value);
    },

    async saveGuide(record: SavedGuideRecord): Promise<void> {
      await guides.register(`${record.experimentId}:${record.savedAt}`, record);
    },
  };
}

function compareExperiments(a: ExperimentRecord, b: ExperimentRecord): number {
  const orderA = a.libraryOrder ?? Number.MAX_SAFE_INTEGER;
  const orderB = b.libraryOrder ?? Number.MAX_SAFE_INTEGER;
  if (orderA !== orderB) {
    return orderA - orderB;
  }
  return a.title.localeCompare(b.title) || a.id.localeCompare(b.id);
}
