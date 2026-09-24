import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { getVersions, type VersionListItem } from "@/lib/api";

/**
 * 版本谱系页（/versions，数据源：Platform 账本，REQ-20260923-145931）。
 *
 * 账本语义（DEC-001/DEC-002）：
 * - 版本号 = 发版流水号，同 commit 重复条目照单全收，标「代码同 vX」
 * - 时间线列表（倒序），不画血缘连线——git 血缘可与版本号倒挂（v13 基于 v14）
 * - 选中详情显示「代码基于：vN」（git 最近账本祖先）/「初始版本」/「解析失败」
 *
 * 账本不可达（DEC-005）：整页报错态 + 重试，不回退冻结 registry 旧数据。
 */
function lineageLabel(item: VersionListItem): string {
  if (item.same_code_as != null) return `代码同 v${item.same_code_as}`;
  if (item.based_on_status === "resolved" && item.based_on != null) {
    return `代码基于：v${item.based_on}`;
  }
  if (item.based_on_status === "error") return "代码基于：解析失败";
  return "初始版本";
}

/** 选中版本的概要展示（账本元数据 + 谱系标注行） */
function VersionSummary({ item }: { item: VersionListItem }) {
  return (
    <div className="card version-summary-card">
      <div className="version-summary-head">
        <h2>
          v{item.version}
          {item.status === "production" && (
            <span className="version-prod-tag" style={{ marginLeft: 10 }}>PRODUCTION</span>
          )}
          {item.status === "retired" && (
            <span className="version-retired-tag mono" style={{ marginLeft: 10 }}>retired</span>
          )}
        </h2>
        <div className="version-summary-meta mono text-dim">
          {`commit ${item.commit.slice(0, 10)} · ${item.created_at.slice(0, 10)}`}
        </div>
      </div>

      <div className="version-lineage-line">{lineageLabel(item)}</div>

      {item.change_summary ? (
        <div className="version-change-summary">{item.change_summary}</div>
      ) : (
        <p className="text-dim" style={{ fontSize: 13, margin: "12px 0 0" }}>
          （无版本说明——发版时未填账本 note）
        </p>
      )}
    </div>
  );
}

export default function VersionsPage() {
  const [items, setItems] = useState<VersionListItem[]>([]);
  const [productionVersion, setProductionVersion] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const resp = await getVersions();
      setItems(resp.items);
      setProductionVersion(resp.production_version);
      // 默认选中 production（无则最新）
      setSelected((prev) => prev ?? resp.production_version ?? resp.items[0]?.version ?? null);
    } catch (err) {
      // DEC-005：报错态替代 toast + 空列表——旧账本数据比没数据危害大
      setLoadError(err instanceof Error ? err.message : "加载版本列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const selectedItem = items.find((v) => v.version === selected) ?? null;
  // M 份不同代码 = 无 same_code_as 标注的条目数（最早持有者是代码身份代表）
  const distinctCount = items.filter((v) => v.same_code_as == null).length;

  return (
    <div className="versions-page">
      <header className="page-header">
        <div className="page-header-row">
          <div>
            <h1>版本谱系</h1>
            <p className="page-desc">
              harness 发版时间线 · 当前生产 v{productionVersion ?? "—"} ·{" "}
              {items.length} 次发版 · {distinctCount} 份不同代码
            </p>
          </div>
          <button
            className="action-link refresh-btn"
            onClick={load}
            title="刷新版本列表"
          >
            <RefreshCw size={14} />
            刷新
          </button>
        </div>
      </header>

      {/* DEC-005：账本不可达报错态（含重试），不渲染版本列表。
          后端 502 detail 已带「Platform 账本不可达/查询异常/解析失败」分类前缀，
          此处只兜底空 message，避免双前缀。 */}
      {loadError ? (
        <div className="evo-state-error">
          {loadError || "Platform 账本不可达"}
          <br />
          <button className="evo-retry-button" onClick={load}>重试</button>
        </div>
      ) : (
        <div className="versions-layout">
          {/* 左：发版时间线（倒序，不画血缘连线——DEC-002） */}
          <div className="versions-list card" style={{ padding: 0 }}>
            <div className="versions-list-head">
              <span className="section-title" style={{ margin: 0 }}>版本</span>
              <span className="text-mute mono" style={{ fontSize: 11 }}>发版时间线</span>
            </div>
            <div className="versions-chain">
              {loading ? (
                <div className="text-mute" style={{ padding: 24, textAlign: "center" }}>
                  加载中…
                </div>
              ) : items.length === 0 ? (
                <div className="text-dim" style={{ padding: 24, textAlign: "center", fontSize: 13 }}>
                  还没有版本。发版后会在此显示。
                </div>
              ) : (
                items.map((v) => {
                  const isProd = v.version === productionVersion;
                  const isSelected = v.version === selected;
                  return (
                    <button
                      key={v.version}
                      className={`version-item ${isSelected ? "selected" : ""} ${isProd ? "production" : ""}`}
                      onClick={() => setSelected(v.version)}
                    >
                      <div className="version-item-head">
                        <span className="version-num mono">v{v.version}</span>
                        {isProd && <span className="version-prod-tag">PRODUCTION</span>}
                        {v.status === "retired" && !isProd && (
                          <span className="version-retired-tag mono">retired</span>
                        )}
                        {v.same_code_as != null && (
                          <span className="version-samecode-tag mono">
                            代码同 v{v.same_code_as}
                          </span>
                        )}
                      </div>
                      <div className="version-summary text-dim">
                        {v.change_summary?.slice(0, 60) || "（无说明）"}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>

          {/* 右：版本概要 */}
          <div className="version-detail">
            {selectedItem ? (
              <VersionSummary item={selectedItem} />
            ) : (
              <div className="card" style={{ padding: 40, textAlign: "center" }}>
                <span className="text-dim">
                  {loading ? "加载中…" : "从左侧选择一个版本"}
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
