import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { HarnessElementView, AgentDiff } from "@/lib/api";
import { AgentBadge } from "./AgentBadge";

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
}: {
  agents: HarnessElementView[];
  diffs: Map<string, AgentDiff> | null;
}) {
  const [showDiff, setShowDiff] = useState(false);

  // 无 diff 数据时禁用开关
  const hasAnySkillsDiff = diffs
    ? agents.some((a) => diffs.get(a.name)?.skills)
    : false;

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

      {agents.map((agent) => {
        // 边缘场景：skills 为空且无删除 → 不显示该段
        const skillsDiff = diffs?.get(agent.name)?.skills ?? null;
        const removedCount = skillsDiff?.removed.length ?? 0;
        if (agent.skills.length === 0 && removedCount === 0) return null;

        return (
          <div key={agent.name} className="element-agent-group">
            <div className="element-agent-group-head">
              <AgentBadge name={agent.name} />
              <h4>
                Skills（{agent.skills.length}
                {showDiff && removedCount > 0 && ` +${removedCount} 已删除`}）
              </h4>
            </div>

            {/* 当前版本 skills：added 卡片头标绿，其余正常 */}
            {agent.skills.map((sk) => {
              const isAdded = !!(showDiff && skillsDiff?.added.includes(sk.path));
              return (
                <SkillCard key={sk.path} skill={sk} added={isAdded} />
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
    </div>
  );
}

/** 单个技能卡片：头部名称/路径/描述 + 可折叠的 SKILL.md 正文（markdown 渲染）。 */
function SkillCard({
  skill,
  added,
}: {
  skill: HarnessElementView["skills"][number];
  added: boolean;
}) {
  // 默认展开——本 Tab 的核心诉求就是看到技能具体内容
  const [open, setOpen] = useState(true);
  const body = skill.content ? stripFrontmatter(skill.content) : "";

  return (
    <div className={`skill-card ${added ? "diff-add" : ""}`}>
      <button
        type="button"
        className="skill-card-head"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="skill-card-name">{skill.name}</span>
        <span className="skill-path">{skill.path}</span>
      </button>
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
