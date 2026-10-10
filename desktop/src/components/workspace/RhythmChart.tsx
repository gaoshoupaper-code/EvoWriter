import { useMemo, useState } from "react";
import type { RhythmData, RhythmPoint, ShapeSlot } from "../../lib/types";

/**
 * 张力曲线（REQ-20261010-000638 FR-006 / DEC-009/012）。
 *
 * 手写 SVG（零图表库依赖）：横轴事件时序、纵轴张力 1~5；
 * 主线/全局合成曲线切换叠加；爽点打标（小=圆点、大=菱形）；
 * 目标形态槽位画浅色背景带；暗线虚线按 T 号与当前视图横轴对齐、
 * 浮出时点打 ▲ 标；点曲线上的点联动全景表定位事件（onEventClick）。
 */

type RhythmChartProps = {
  rhythm: RhythmData;
  onEventClick?: (eventName: string) => void;
};

const VIEW_W = 760;
const VIEW_H = 240;
const PAD = { top: 34, right: 18, bottom: 34, left: 40 };

// 槽位 → 横轴背景带区间（比例；首事件带取最前一小段，其余三等分）
const SLOT_BANDS: Record<string, [number, number]> = {
  首事件: [0, 1 / 6],
  前段末: [1 / 6, 1 / 3],
  中点谷: [1 / 3, 2 / 3],
  终局: [2 / 3, 1],
};

const _T_PARSE = /^T?\s*(\d+(?:\.\d+)?)\s*$/;

function tNum(t: string): number | null {
  const m = _T_PARSE.exec(t.trim());
  return m ? parseFloat(m[1]) : null;
}

function slotLabel(s: ShapeSlot): string {
  const v = s.twin_peak ? `双峰${s.values.join(",")}` : `${s.op}${s.values.join(",")}`;
  return `${s.slot}${v}`;
}

/** 大爽菱形标记的顶点（cx/cy + 半径 r）。 */
function diamondPoints(cx: number, cy: number, r: number): string {
  return `${cx},${cy - r} ${cx + r},${cy} ${cx},${cy + r} ${cx - r},${cy}`;
}

export function RhythmChart({ rhythm, onEventClick }: RhythmChartProps) {
  const [view, setView] = useState<"mainline" | "synthesis">("mainline");
  const series = view === "mainline" ? rhythm.mainline : rhythm.synthesis;
  const points = useMemo(() => series.filter((p) => p.tension != null), [series]);
  const nullCount = series.length - points.length;

  const plotW = VIEW_W - PAD.left - PAD.right;
  const plotH = VIEW_H - PAD.top - PAD.bottom;
  const n = Math.max(points.length - 1, 1);
  const x = (i: number) => PAD.left + (i / n) * plotW;
  const y = (t: number) => PAD.top + (1 - t / 5) * plotH;
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)} ${y(p.tension ?? 0).toFixed(1)}`)
    .join(" ");

  // X 轴稀疏标注（事件多时不拥挤）
  const labelStep = Math.max(1, Math.ceil(points.length / 12));

  // 暗线横轴对齐：按 T 号在当前视图序列中插值取 x（与主线/合成同轴）；
  // T 号不可解析的点跳过（断线），避免把暗线拉到错误时序位置
  const xByT = (v: number): number => {
    const ts = points.map((p) => tNum(p.t));
    let idx = 0;
    for (let i = 0; i < ts.length; i++) {
      if (ts[i] != null && (ts[i] as number) < v) idx = i + 1;
    }
    const prev = idx - 1 >= 0 ? ts[idx - 1] : null;
    const next = idx < ts.length ? ts[idx] : null;
    if (prev != null && next != null && (next as number) > (prev as number)) {
      const f = (v - (prev as number)) / ((next as number) - (prev as number));
      return x(idx - 1) + f * (x(idx) - x(idx - 1));
    }
    if (idx >= points.length) return x(points.length - 1);
    if (idx === 0) return x(0);
    return x(idx);
  };

  const darkLines = Object.entries(rhythm.dark).map(([line, pts]) => {
    const aligned = pts
      .map((p) => ({ p, tv: tNum(p.t) }))
      .filter((a) => a.p.tension != null && a.tv != null) as { p: RhythmPoint; tv: number }[];
    const sorted = [...aligned].sort((a, b) => a.tv - b.tv);
    return {
      line,
      d: sorted
        .map((a, i) => `${i === 0 ? "M" : "L"}${xByT(a.tv).toFixed(1)} ${y(a.p.tension ?? 0).toFixed(1)}`)
        .join(" "),
      surfaces: sorted.filter((a) => a.p.surface),
    };
  });
  const hasSurface = darkLines.some((d) => d.surfaces.length > 0);

  const marker = (p: RhythmPoint, i: number) => {
    if (p.payoff !== "小" && p.payoff !== "大") return null;
    const cx = x(i);
    const cy = y(p.tension ?? 0);
    const big = p.payoff === "大";
    return (
      <g
        key={`${p.name}-payoff`}
        role="button"
        aria-label={`${p.name}（${p.payoff}爽，张力${p.tension}）`}
        style={{ cursor: onEventClick ? "pointer" : "default" }}
        onClick={() => onEventClick?.(p.name)}
      >
        <title>{`${p.name}｜${p.payoff}爽｜张力 ${p.tension}`}</title>
        {big ? (
          <polygon points={diamondPoints(cx, cy, 6)} fill="#f59e0b" stroke="#b45309" strokeWidth={1} />
        ) : (
          <circle cx={cx} cy={cy} r={4} fill="#fbbf24" stroke="#b45309" strokeWidth={1} />
        )}
        {/* 扩大点击热区 */}
        <circle cx={cx} cy={cy} r={10} fill="transparent" />
      </g>
    );
  };

  return (
    <div className="rhythm-chart" aria-label="节奏区块">
      <div className="rhythm-chart-toolbar">
        <span className="field-label">节奏</span>
        <div className="rhythm-chart-toggle" role="group" aria-label="曲线切换">
          <button
            type="button"
            aria-pressed={view === "mainline"}
            className={`rhythm-toggle-btn${view === "mainline" ? " active" : ""}`}
            onClick={() => setView("mainline")}
          >
            主线
          </button>
          <button
            type="button"
            aria-pressed={view === "synthesis"}
            className={`rhythm-toggle-btn${view === "synthesis" ? " active" : ""}`}
            onClick={() => setView("synthesis")}
          >
            全局合成
          </button>
        </div>
        {rhythm.shape_slots.length > 0 ? (
          <span className="rhythm-shape-label">
            目标形态：{rhythm.shape_slots.map(slotLabel).join(" · ")}
          </span>
        ) : null}
      </div>

      {points.length === 0 ? (
        <p className="field-label">当前视图无张力数据。</p>
      ) : (
        <svg
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          role="img"
          aria-label="张力曲线：横轴事件时序，纵轴张力 1 到 5"
          style={{ width: "100%", height: "auto", display: "block" }}
        >
          {/* 形态槽位背景带 */}
          {rhythm.shape_slots.map((s) => {
            const band = SLOT_BANDS[s.slot];
            if (!band) return null;
            const bx = PAD.left + band[0] * plotW;
            const bw = (band[1] - band[0]) * plotW;
            return (
              <g key={s.slot}>
                <rect x={bx} y={PAD.top} width={bw} height={plotH} fill="currentColor" opacity={0.05} />
                <text x={bx + 3} y={PAD.top - 8} fontSize={10} opacity={0.65}>
                  {slotLabel(s)}
                </text>
              </g>
            );
          })}

          {/* 纵轴网格（张力 1~5） */}
          {[1, 2, 3, 4, 5].map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={VIEW_W - PAD.right} y1={y(t)} y2={y(t)} stroke="currentColor" opacity={0.12} strokeWidth={1} />
              <text x={PAD.left - 8} y={y(t) + 3} fontSize={10} textAnchor="end" opacity={0.7}>
                {t}
              </text>
            </g>
          ))}

          {/* 暗线（虚线，按 T 号与当前视图同轴对齐；▲=浮出时点） */}
          {darkLines.map((dl) => (
            <g key={dl.line}>
              <path d={dl.d} fill="none" stroke="#a855f7" strokeWidth={1.5} strokeDasharray="5 4" opacity={0.8}>
                <title>{`暗线：${dl.line}`}</title>
              </path>
              {dl.surfaces.map((a) => (
                <polygon
                  key={`${dl.line}-surface-${a.p.name}`}
                  points={diamondPoints(xByT(a.tv), y(a.p.tension ?? 0) - 10, 5)}
                  fill="#a855f7"
                  opacity={0.9}
                >
                  <title>{`浮出时点：${a.p.name}（${dl.line}）`}</title>
                </polygon>
              ))}
            </g>
          ))}

          {/* 当前视图曲线 */}
          <path d={path} fill="none" stroke={view === "mainline" ? "#2563eb" : "#0d9488"} strokeWidth={2} />

          {/* 数据点 + 点击联动 */}
          {points.map((p, i) => (
            <g
              key={p.name}
              role="button"
              aria-label={`${p.name}，张力${p.tension}`}
              style={{ cursor: onEventClick ? "pointer" : "default" }}
              onClick={() => onEventClick?.(p.name)}
            >
              <title>{`${p.name}｜${p.line}｜张力 ${p.tension}${p.payoff && p.payoff !== "—" ? `｜${p.payoff}爽` : ""}`}</title>
              <circle cx={x(i)} cy={y(p.tension ?? 0)} r={3} fill={view === "mainline" ? "#2563eb" : "#0d9488"} />
              <circle cx={x(i)} cy={y(p.tension ?? 0)} r={9} fill="transparent" />
            </g>
          ))}

          {/* 爽点标记 */}
          {points.map((p, i) => marker(p, i))}

          {/* X 轴稀疏标注 */}
          {points.map((p, i) =>
            i % labelStep === 0 || i === points.length - 1 ? (
              <text key={`${p.name}-x`} x={x(i)} y={VIEW_H - PAD.bottom + 14} fontSize={10} textAnchor="middle" opacity={0.7}>
                {p.t}
              </text>
            ) : null,
          )}
        </svg>
      )}

      <p className="rhythm-legend field-label">
        实线=当前曲线（蓝：主线 / 青：全局合成）；菱形=大爽点、圆点=小爽点；虚线=暗线（读者不可见，不计入合成）
        {hasSurface ? "，▲=暗线浮出时点" : ""}。
      </p>
      {nullCount > 0 ? (
        <p className="rhythm-legend field-label" aria-label="缺张力提示">
          {nullCount} 个事件无张力标注（旧版本区块），未计入曲线。
        </p>
      ) : null}

      {rhythm.hooks.length > 0 ? (
        <div className="rhythm-hooks">
          <span className="field-label">钩子登记</span>
          <div className="md-table-wrap">
            <table aria-label="钩子登记">
              <thead>
                <tr>
                  <th>编号</th>
                  <th>钩子</th>
                  <th>层级</th>
                  <th>类型</th>
                  <th>状态</th>
                  <th>埋设 → 推进 → 兑现</th>
                </tr>
              </thead>
              <tbody>
                {rhythm.hooks.map((hk) => (
                  <tr key={hk.id}>
                    <td>{hk.id}</td>
                    <td>{hk.text}</td>
                    <td>{hk.level}</td>
                    <td>{hk.type}</td>
                    <td>{hk.status}</td>
                    <td>
                      {hk.plant_events.join("、") || "—"} → {hk.progress_events.join("、") || "—"} → {hk.payoff_events.join("、") || "未收"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}
