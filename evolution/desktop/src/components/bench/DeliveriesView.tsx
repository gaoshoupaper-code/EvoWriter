import { useEffect, useRef, useState } from "react";
import { Download, FileText, LoaderCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  getArtifactRevisionContent,
  getBenchmarkDeliveries,
  type BenchmarkDeliveriesResponse,
} from "@/lib/api";
import { downloadTextFile } from "@/lib/download";

/**
 * 大纲三件套渲染视图（REQ-20260921-135543 FR-006，DEC-022 复用组件）。
 *
 * 批次详情（评分依据内嵌）与产物库（产物为主角）共用：
 * - 正文 Markdown 渲染（与 trace 详情页同款 react-markdown + remark-gfm）
 * - 无权/过期/缺失条目保持可见 + 状态徽章（DEC-015）
 * - focusHint：维度名 → 滚动定位相关交付分组（DEC-011 静态映射）
 * - 单篇导出（FR-009：Blob 下载）
 */

/** 维度 → 三件套分组映射（DEC-011：judge 理由无结构化引用，静态映射）。 */
export const DIMENSION_GROUP_HINTS: Record<string, string[]> = {
  设定自洽: ["世界观"],
  人物塑造: ["人物"],
  情节构造: ["主线"],
  节奏结构: ["主线"],
  // 需求兑现 → 全部分组（不在此表，调用方传 undefined）
};

export function groupsForDimension(dim: string): string[] | null {
  return DIMENSION_GROUP_HINTS[dim] ?? null;
}

type ContentState =
  | { status: "loading" }
  | { status: "open"; content: unknown }
  | { status: "error"; message: string };

export function DeliveriesView({
  traceId,
  focusHint,
}: {
  traceId: string;
  /** 需要定位高亮的分组关键词（如「世界观」）；变化时滚动到对应分组 */
  focusHint?: string | null;
}) {
  const [data, setData] = useState<BenchmarkDeliveriesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [contents, setContents] = useState<Record<string, ContentState>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const groupRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setContents({});
    setExpanded(new Set());
    getBenchmarkDeliveries(traceId)
      .then((resp) => {
        if (!cancelled) setData(resp);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [traceId]);

  // 维度卡片「查看相关交付」跳转：滚动 + 展开目标分组（DEC-011）。
  // focusHint 形如 "世界观:<nonce>"——CaseRunsPanel 拼 nonce 让同分组重复点击
  // 也能再次触发本 effect，匹配关键词前先剥掉。
  useEffect(() => {
    if (!focusHint || !data) return;
    const keyword = focusHint.split(":")[0];
    if (!keyword) return;
    const target = data.groups.find((g) => g.display.includes(keyword));
    if (!target) return;
    const el = groupRefs.current[target.display];
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
    el?.classList.add("bench-delivery-focus");
    const timer = setTimeout(() => el?.classList.remove("bench-delivery-focus"), 2000);
    // 2 秒内切换到其他分组时，cleanup 立即清掉旧高亮（防残留）
    return () => {
      clearTimeout(timer);
      el?.classList.remove("bench-delivery-focus");
    };
  }, [focusHint, data]);

  function toggleFile(revisionId: string) {
    const next = new Set(expanded);
    if (next.has(revisionId)) {
      next.delete(revisionId);
      setExpanded(next);
      return;
    }
    next.add(revisionId);
    setExpanded(next);
    if (contents[revisionId]) return; // 已加载过，直接展开
    setContents((prev) => ({ ...prev, [revisionId]: { status: "loading" } }));
    getArtifactRevisionContent(revisionId)
      .then((resp) => {
        setContents((prev) => ({
          ...prev,
          [revisionId]: { status: "open", content: resp.content },
        }));
      })
      .catch((e: unknown) => {
        setContents((prev) => ({
          ...prev,
          [revisionId]: {
            status: "error",
            message: e instanceof Error ? e.message : String(e),
          },
        }));
      });
  }

  function exportFile(logicalKey: string, revisionId: string) {
    const entry = contents[revisionId];
    if (entry?.status !== "open") return; // 仅已加载正文可导出
    const content = typeof entry.content === "string"
      ? entry.content
      : JSON.stringify(entry.content, null, 2);
    const safeName = logicalKey.replace(/[\\/]/g, "_");
    downloadTextFile(`${safeName}.md`, content);
  }

  if (loading) return <div className="page-loading">加载三件套…</div>;
  if (error) {
    return <div className="monitor-empty">读取三件套失败：{error}</div>;
  }

  return (
    <div className="bench-delivery">
      <h5>大纲三件套（评分依据）</h5>
      {!data?.can_read_content && (
        <div className="bench-delivery-note">无权查看正文（仅超级管理员可按需读取）。</div>
      )}
      {data?.groups.map((group) => (
        <div
          key={group.display}
          ref={(el) => {
            groupRefs.current[group.display] = el;
          }}
          className="bench-delivery-group"
        >
          <div className="bench-delivery-head">
            <FileText size={13} aria-hidden />
            <span>{group.display}</span>
          </div>
          {group.files.length === 0 ? (
            <div className="bench-delivery-note">
              <span className="bench-delivery-badge missing">缺失</span>
            </div>
          ) : (
            group.files.map((file) => {
              const revisionId = file.artifact_revision_id ?? "";
              const isOpen = expanded.has(revisionId);
              const state = contents[revisionId];
              const canOpen = data.can_read_content && file.available && revisionId !== "";
              return (
                <div key={`${file.logical_key}:${revisionId}`} className="bench-delivery-file">
                  <div className="bench-delivery-file-row">
                    <button
                      type="button"
                      className="bench-delivery-toggle"
                      disabled={!canOpen}
                      onClick={() => toggleFile(revisionId)}
                      title={
                        !data.can_read_content
                          ? "无权查看正文"
                          : !file.available
                            ? "正文已过期（保留 90 天）"
                            : isOpen
                              ? "收起正文"
                              : "查看正文"
                      }
                    >
                      <span className="bench-delivery-path" title={file.logical_key}>
                        {file.logical_key}
                      </span>
                      {state?.status === "loading" ? (
                        <LoaderCircle className="artifact-content-spinner" size={13} />
                      ) : !file.available ? (
                        <span className="bench-delivery-badge expired">正文已过期（保留 90 天）</span>
                      ) : !data.can_read_content ? (
                        <span className="bench-delivery-badge locked">无权</span>
                      ) : (
                        <span className="bench-delivery-note">{isOpen ? "收起" : "正文"}</span>
                      )}
                    </button>
                    {canOpen && isOpen && state?.status === "open" && (
                      <button
                        type="button"
                        className="action-link bench-delivery-export"
                        title="下载该篇 Markdown"
                        onClick={() => exportFile(file.logical_key, revisionId)}
                      >
                        <Download size={13} aria-hidden />
                        导出
                      </button>
                    )}
                  </div>
                  {state?.status === "error" && (
                    <div className="bench-delivery-note">读取正文失败：{state.message}</div>
                  )}
                  {isOpen && state?.status === "open" && (
                    <ContentBody content={state.content} />
                  )}
                </div>
              );
            })
          )}
        </div>
      ))}
    </div>
  );
}

function ContentBody({ content }: { content: unknown }) {
  if (typeof content === "string") {
    return (
      <div className="bench-delivery-content prose-doc">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    );
  }
  return <pre className="bench-delivery-content">{JSON.stringify(content, null, 2)}</pre>;
}
