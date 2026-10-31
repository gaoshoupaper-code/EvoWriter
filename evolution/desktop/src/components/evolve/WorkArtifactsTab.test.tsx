import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  getEvolveAgentArtifactContent,
  getEvolveAgentArtifactRevisionContent,
  getEvolveAgentArtifacts,
  listEvolveAgents,
} from "@/lib/api";
import WorkArtifactsTab from "./WorkArtifactsTab";

vi.mock("@/lib/api", () => ({
  getEvolveAgentArtifactContent: vi.fn(),
  getEvolveAgentArtifactRevisionContent: vi.fn(),
  getEvolveAgentArtifacts: vi.fn(),
  listEvolveAgents: vi.fn(),
}));

const selectedAgentId = "agent-1";
const revisionId = "artifact-rev-abcdefghijklmnopqrstuvwxyz123456";
const artifactResponse = {
  agent_id: selectedAgentId,
  workspace_id: "workspace-1",
  current: {
    kind: "five" as const,
    category: null,
    offset: 0,
    limit: 100,
    total: 1,
    files: [{ path: "正文/第001章.md", category: "chapter_text" as const, size_bytes: 12 }],
  },
  revisions: [{
    logical_key: "/正文/第001章.md",
    revision_count: 1,
    head_revision_id: revisionId,
    revisions: [{
      artifact_revision_id: revisionId,
      content_hash: "hash-1",
      producer_trace_id: "trace-1",
      producer_event_id: "event-1",
      harness_version: "abcdef123456",
      created_at: "2026-10-01T00:00:00Z",
      artifact_type: "workspace_file",
      size_bytes: 12,
      expires_at: "2099-01-01T00:00:00Z",
      deleted_at: null,
    }],
  }],
  revision_total: 1,
  revision_offset: 0,
  revision_limit: 100,
  can_read_content: true,
};

function renderTab(canReadContent: boolean) {
  return render(
    <WorkArtifactsTab
      selectedAgentId={selectedAgentId}
      onSelectedAgentChange={vi.fn()}
      canReadContent={canReadContent}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listEvolveAgents).mockResolvedValue({
    agents: [{
      agent_id: selectedAgentId,
      name: "产物 Agent",
      workspace_id: "workspace-1",
      status: "active",
      work_deleted_at: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: null,
    }],
    total: 1,
  });
  vi.mocked(getEvolveAgentArtifacts).mockResolvedValue(artifactResponse);
  vi.mocked(getEvolveAgentArtifactContent).mockResolvedValue({
    agent_id: selectedAgentId,
    workspace_id: "workspace-1",
    path: "正文/第001章.md",
    content: "当前章节正文",
  });
  vi.mocked(getEvolveAgentArtifactRevisionContent).mockResolvedValue({
    artifact_revision_id: revisionId,
    logical_key: "/正文/第001章.md",
    created_at: "2026-10-01T00:00:00Z",
    content_hash: "hash-1",
    content: "历史修订正文",
  });
});

describe("WorkArtifactsTab", () => {
  it("普通管理员可查看当前文件和修订元数据，但不能请求正文", async () => {
    renderTab(false);
    expect(await screen.findByText("正文/第001章.md")).toBeTruthy();
    expect(screen.getByText(revisionId)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /正文\/第001章\.md/ }));
    expect(await screen.findByText("完整作品正文仅超级管理员可查看。", { exact: false })).toBeTruthy();
    expect(getEvolveAgentArtifactContent).not.toHaveBeenCalled();
    expect(getEvolveAgentArtifactRevisionContent).not.toHaveBeenCalled();
  });

  it("超级管理员可读取当前文件和用完整 ID 定位的历史正文", async () => {
    renderTab(true);
    const currentRow = await screen.findByRole("button", { name: /正文\/第001章\.md/ });
    fireEvent.click(currentRow);
    expect(await screen.findByText("当前章节正文")).toBeTruthy();
    expect(getEvolveAgentArtifactContent).toHaveBeenCalledWith(selectedAgentId, "正文/第001章.md");

    fireEvent.click(screen.getByRole("button", { name: new RegExp(revisionId) }));
    expect(await screen.findByText("历史修订正文")).toBeTruthy();
    expect(getEvolveAgentArtifactRevisionContent).toHaveBeenCalledWith(selectedAgentId, revisionId);
  });

  it("当前文件服务不可达时仍显示作品侧历史修订", async () => {
    vi.mocked(getEvolveAgentArtifacts).mockResolvedValue({
      ...artifactResponse,
      current: { ...artifactResponse.current, kind: "unreachable", files: [], total: 0, error: "连接超时" },
    });
    renderTab(false);
    expect(await screen.findByText(/当前文件暂时无法读取：连接超时/)).toBeTruthy();
    expect(screen.getByText(revisionId)).toBeTruthy();
  });
});
