import { useLayoutEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { HarnessElementView, AgentDiff } from "@/lib/api";
import type { SaveManualAsset } from "@/lib/manual-assets-api";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { AgentBadge } from "./AgentBadge";
import { AssetEditSheet } from "./AssetEditSheet";

type Skill = HarnessElementView["skills"][number];

/**
 * Skills Tab：按 agent 分段展示各 agent 的技能卡片。
 *
 * - 每个技能一张可折叠卡片：头部 = 名称 + 路径 + frontmatter 描述，
 *   身体 = SKILL.md 全文（剥 frontmatter）markdown 渲染
 * - 默认展开——要素页的价值就是看到具体内容，收起只作长文收纳
 * - Tab 内独立"显示 diff"开关（D18），开启后：新增技能卡片头绿底、
 *   删除路径红底行（D13，从 diff 数据补出来渲染）
 * - 每段标 Agent 徽章（D5）
 */

/** 剥掉 SKILL.md 开头的 YAML frontmatter（--- 包裹块），返回正文。 */
function stripFrontmatter(content: string): string {
  if (!content.startsWith("---")) return content;
  // 闭合 fence = 独占一行的 "---"；找不到（未闭合）时原样返回，宁可多显示不吞正文
  const end = content.indexOf("\n---", 3);
  if (end === -1) return content;
  return content.slice(end + 4).replace(/^\n+/, "");
}

export function SkillsTab({
  agents,
  diffs,
  onSave,
  busy = false,
}: {
  agents: HarnessElementView[];
  diffs: Map<string, AgentDiff> | null;
  onSave?: SaveManualAsset;
  busy?: boolean;
}) {
  const [showDiff, setShowDiff] = useState(false);
  const [action, setAction] = useState<{ type: "edit" | "delete"; role: string; skill: Skill } | null>(null);
  const [importing, setImporting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(false);

  useLayoutEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  async function importSkill(role: string, file: File) {
    if (!onSave) return;
    setError("");
    if (file.name !== "SKILL.md") {
      setError("只支持导入 SKILL.md。");
      return;
    }
    setImporting(true);
    try {
      let content: string;
      try {
        content = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
      } catch {
        throw new Error("无法读取 SKILL.md。请检查文件是否为 UTF-8。");
      }
      if (!active.current) return;
      await onSave({ role, operation: "skill_import", filename: file.name, content });
    } catch (cause) {
      if (active.current) setError(cause instanceof Error ? cause.message : "导入技能失败。");
    } finally {
      if (active.current) setImporting(false);
    }
  }

  async function deleteSkill() {
    if (!action || !onSave) return;
    setError("");
    setDeleting(true);
    try {
      await onSave({ role: action.role, operation: "skill_delete", path: action.skill.path });
      setAction(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除技能失败。");
    } finally {
      setDeleting(false);
    }
  }

  // 无 diff 数据时禁用开关
  const hasAnySkillsDiff = diffs
    ? agents.some((a) => diffs.get(a.name)?.skills)
    : false;

  // 空态：当前视角所有 agent 均无技能且无删除记录（v48 起架构未用 skills 目录，
  // 诚实提示而非渲染空白页，避免误判为加载失败）
  const hasAnySkills = agents.some(
    (a) => a.skills.length > 0 || (diffs?.get(a.name)?.skills?.removed.length ?? 0) > 0,
  );

  return (
    <div>
      <div className="diff-toggle-bar">
        <div
          className={`diff-toggle ${showDiff && hasAnySkillsDiff ? "on" : ""}`}
          onClick={() => hasAnySkillsDiff && setShowDiff(!showDiff)}
          role="switch"
          aria-checked={showDiff && hasAnySkillsDiff}
          style={!hasAnySkillsDiff ? { opacity: 0.4, cursor: "not-allowed" } : undefined}
        />
        <span>显示升级差异{!hasAnySkillsDiff && "（本版本无 skills 变更）"}</span>
      </div>

      {!hasAnySkills && !onSave && (
        <div className="monitor-empty">该版本所有 Agent 均无 skills。</div>
      )}
      {!action && error && <p className="skill-load-error" role="alert">{error}</p>}

      {agents.map((agent) => {
        // 边缘场景：skills 为空且无删除 → 不显示该段
        const skillsDiff = diffs?.get(agent.name)?.skills ?? null;
        const removedCount = skillsDiff?.removed.length ?? 0;
        if (!onSave && agent.skills.length === 0 && removedCount === 0) return null;

        return (
          <div key={agent.name} className="element-agent-group">
            <div className="element-agent-group-head">
              <AgentBadge name={agent.name} />
              <h4>
                Skills（{agent.skills.length}
                {showDiff && removedCount > 0 && ` +${removedCount} 已删除`}）
              </h4>
              {onSave && (
                <label className="prompt-file-edit manual-skill-import">
                  {importing ? "导入中…" : "导入 SKILL.md"}
                  <input
                    aria-label="导入 SKILL.md"
                    type="file"
                    accept=".md"
                    disabled={busy || importing}
                    onChange={(event) => {
                      const file = event.currentTarget.files?.[0];
                      event.currentTarget.value = "";
                      if (file) void importSkill(agent.name, file);
                    }}
                  />
                </label>
              )}
            </div>

            {onSave && agent.skills.length === 0 && <p className="monitor-empty">当前角色没有技能。</p>}
            {/* 当前版本 skills：added 卡片头标绿，其余正常 */}
            {agent.skills.map((sk) => {
              const isAdded = !!(showDiff && skillsDiff?.added.includes(sk.path));
              return (
                <SkillCard
                  key={sk.path}
                  skill={sk}
                  added={isAdded}
                  disabled={busy || importing}
                  onAction={onSave ? (type) => {
                    setError("");
                    setAction({ type, role: agent.name, skill: sk });
                  } : undefined}
                />
              );
            })}

            {/* 删除的 skills：当前版本没有，从 diff 补出来标红 */}
            {showDiff &&
              skillsDiff?.removed.map((path, i) => (
                <div key={`del-${i}`} className="skill-row diff-del">
                  <span className="skill-path">{path}</span>
                </div>
              ))}
          </div>
        );
      })}
      {action?.type === "edit" && (
        <AssetEditSheet
          title={action.skill.name}
          content={action.skill.content ?? ""}
          disabled={!onSave || busy}
          onClose={() => setAction(null)}
          onSave={async (content) => {
            await onSave?.({ role: action.role, operation: "skill_edit", path: action.skill.path, content });
          }}
        />
      )}
      {action?.type === "delete" && (
        <Sheet open onOpenChange={(open) => !open && !deleting && setAction(null)}>
          <SheetContent side="right" aria-describedby={undefined}>
            <SheetHeader><SheetTitle>删除技能 · {action.skill.name}</SheetTitle></SheetHeader>
            <div className="manual-draft-body">
              <p>从当前角色的草稿移除该技能。</p>
              {error && <p className="skill-load-error" role="alert">{error}</p>}
              <div className="prompt-edit-actions">
                <button type="button" onClick={deleteSkill} disabled={!onSave || busy || deleting}>确认删除</button>
                <button type="button" onClick={() => setAction(null)} disabled={deleting}>取消</button>
              </div>
            </div>
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}

/** 单个技能卡片：头部名称/路径/描述 + 可折叠的 SKILL.md 正文（markdown 渲染）。 */
function SkillCard({
  skill,
  added,
  disabled,
  onAction,
}: {
  skill: Skill;
  added: boolean;
  disabled: boolean;
  onAction?: (type: "edit" | "delete") => void;
}) {
  // 默认展开——本 Tab 的核心诉求就是看到技能具体内容
  const [open, setOpen] = useState(true);
  const body = skill.content ? stripFrontmatter(skill.content) : "";

  return (
    <div className={`skill-card ${added ? "diff-add" : ""}`}>
      <div className="skill-card-head">
        <button
          type="button"
          className="prompt-file-toggle"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span className="skill-card-name">{skill.name}</span>
          <span className="skill-path">{skill.path}</span>
        </button>
        {onAction && (
          <>
            <button type="button" className="prompt-file-edit" onClick={() => onAction("edit")} disabled={disabled || skill.content === null}>编辑</button>
            <button type="button" className="prompt-file-edit" onClick={() => onAction("delete")} disabled={disabled}>删除</button>
          </>
        )}
      </div>
      <div className="skill-card-body">
        {skill.description && <p className="skill-desc">{skill.description}</p>}
        {skill.load_error && <p className="skill-load-error">⚠ {skill.load_error}</p>}
        {open && (
          body ? (
            <div className="prose-doc skill-doc">
              <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
                {body}
              </ReactMarkdown>
            </div>
          ) : (
            !skill.load_error && <p className="prompt-empty">（无正文内容）</p>
          )
        )}
      </div>
    </div>
  );
}
