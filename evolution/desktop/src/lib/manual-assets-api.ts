import { evoJson, type HarnessElementView, type PromptFileInfo } from "./api";

const draftPath = "/api/harness/manual-draft";

export interface ManualDraftRevision {
  base_version: number;
  revision: number;
}

export type ManualAssetOperation = "prompt" | "skill_edit" | "skill_import" | "skill_delete";

export type ManualAssetMutation = { role: string } & (
  | { operation: "prompt" | "skill_edit"; path: string; content: string }
  | { operation: "skill_delete"; path: string }
  | { operation: "skill_import"; filename: string; content: string }
);

export type SaveManualAsset = (change: ManualAssetMutation) => Promise<void>;

export interface ManualAssetChange {
  role: string;
  name: string;
  operation: ManualAssetOperation;
  before: string | null;
  after: string | null;
}

export interface ManualAssetDraft extends ManualDraftRevision {
  base_commit: string;
  state: "editing" | "stale" | "unconfirmed" | "activated";
  agents: {
    name: string;
    prompt_files: PromptFileInfo[];
    skills: HarnessElementView["skills"];
  }[] | null;
  changes: ManualAssetChange[];
  message?: string;
  published_version?: number;
}

export function getManualAssetDraft() {
  return evoJson<ManualAssetDraft>(draftPath, { method: "GET" });
}

export function saveManualAssetDraft(draft: ManualDraftRevision, change: ManualAssetMutation) {
  return evoJson<ManualAssetDraft>(draftPath, {
    method: "PUT",
    body: JSON.stringify({ base_version: draft.base_version, revision: draft.revision, ...change }),
  });
}

export function discardManualAssetDraft(draft: ManualDraftRevision) {
  return evoJson<ManualAssetDraft>(draftPath, {
    method: "DELETE",
    body: JSON.stringify({ base_version: draft.base_version, revision: draft.revision }),
  });
}

export function releaseManualAssetDraft(draft: ManualDraftRevision) {
  return evoJson<ManualAssetDraft>(`${draftPath}/release`, {
    method: "POST",
    body: JSON.stringify({ base_version: draft.base_version, revision: draft.revision }),
  });
}
