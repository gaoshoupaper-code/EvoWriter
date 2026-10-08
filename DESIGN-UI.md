# Writer UI 设计规范(DESIGN-UI)

本文件是两端桌面应用(写作端 `desktop/`、进化端 `evolution/desktop/`)的视觉设计规范,
面向 coding agent 与开发者。改两端 UI 前先读本文;违反本文视为设计缺陷,不是风格偏好。
体系源自 ZCode 开源设计系统(zai-org/ZCode 的 DESIGN.md),按 Writer 纸感风格适配。

配套扫描脚本:`python scripts/design_lint.py`(违规清单 + 退出码)。
新代码合入前脚本必须零违规。

## 最高优先约束:字号只有 token 一个源头

界面文字**必须**使用 `--text-ui-*` token,禁止硬编码字号:

- CSS 里禁止 `font-size: 13px` 之类数值写法,必须 `font-size: var(--text-ui-*)`。
- TSX 里禁止 `style={{ fontSize: 14 }}` 数值写法,必须字符串 `"var(--text-ui-*)"`。
- 唯一基准:`--ui-font-size`(默认 14px)。全局缩放只改这一个变量,
  禁止改 `html` 的 font-size。
- 图标、间距、圆角等几何**不**随字号缩放。

内容例外区(保留独立排版字号,不算违规):

- Markdown 渲染区(文章正文、预览)
- 代码块、diff、终端式输出
- 展示型大字(≥24px 的统计数字、登录页主标题)——逐处加 `design-lint:allow` 注释

## 字号 token 表

| Token | 公式 | 默认值 | 语义角色 |
|---|---|---|---|
| `--text-ui-display` | +8px | 22px | 页面级大标题、大统计数字(20~22px 场景) |
| `--text-ui-xl` | +4px | 18px | 区块主标题、一级阅读标题 |
| `--text-ui-lg` | +2px | 16px | 卡片标题、二级标题(15/16px 场景归此档) |
| `--text-ui-base` | 0 | 14px | 正文、按钮、常规标签(默认档) |
| `--text-ui-caption` | -1px | 13px | 比正文低一档的紧凑文字 |
| `--text-ui-sm` | -2px | 12px | 辅助说明、次要信息、tooltip(11~12.5px 场景归此档) |
| `--text-ui-xs` | -4px | 10px | 徽章、计数器、极弱元信息 |
| `--text-ui-2xs` | -5px | 9px | 极小徽章、图表刻度(8/9px 场景归此档) |

规则:

- 层级优先靠**字重**区分(标题 semibold、正文 normal),不靠字号堆砌。
- 旧像素值迁移映射:8/9→2xs;10/10.5→xs;11~12.5→sm;13/13.5→caption;
  14→base;15/16→lg;17~19→xl;20~22→display;≥24 逐处豁免。
- 字号档位与颜色档位(主文/次文/弱提示)是独立决策,分开选。

## 字体栈

界面字体(两端 body):

```css
--font-sans: "MiSans", "HarmonyOS Sans SC", "PingFang SC",
  "Microsoft YaHei UI", "Microsoft YaHei", Inter, ui-sans-serif, system-ui, sans-serif;
```

- MiSans 由应用内嵌打包(`public/fonts/`,@font-face 引入,woff2 优先),
  不依赖用户本机安装;加载失败自动回落雅黑/苹方。
- 字体文件只打包实际用到的字重(Regular 400 / Medium 500 / Semibold 600)。

等宽字体(代码、路径、hash、终端式输出):

```css
--font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
  "Liberation Mono", "Courier New", "Microsoft YaHei UI", "Microsoft YaHei",
  "PingFang SC", "Noto Sans CJK SC", monospace;
```

- 等宽场景**必须**用 `var(--font-mono)`,禁止裸 `monospace` 或手写栈。
  栈内中文字体是 Windows 上代码块中文不掉宋体的关键,顺序不得改动。

## 圆角:按嵌套层级递减

Token 四档:`--radius-sm: 6px` / `--radius-md: 8px` / `--radius-lg: 12px` / `--radius-xl: 16px`。

- 同级容器同圆角;嵌套逐层降档:外壳 xl → 卡片 lg → 控件 md → 内嵌小件 sm。
- 第一个可见圆角容器从 lg 或 xl 起;基础控件(按钮/输入框)默认 md。
- 胶囊形用 `999px`,圆形用 `50%`,仅限真实的 pill/圆,不得当通用圆角。
- 禁止散落数值圆角(`border-radius: 10px` 之类);迁移映射:
  4~6→sm;7~9→md;10~14→lg;≥15→xl;999px/50%/0 保留。

## 阴影:白名单制

普通面板与卡片**零阴影**。分层靠:背景深浅 + 半透明边框 + 圆角嵌套。

阴影只允许出现在三类浮层(值必须用 token):

- 弹窗/对话框:`box-shadow: var(--shadow-elevated)`
- 菜单/下拉:`box-shadow: var(--shadow-soft)`
- toast:`box-shadow: var(--shadow-elevated)`

其余任何 `box-shadow` 都是违规。需要强调层级时优先加边框或换背景色。

## 动效:快而低调

```css
--motion-duration: 140ms;
--motion-ease: ease-out;
```

- 所有 `transition` 时长与缓动**必须**用上述 token,禁止散落 `0.15s`、`0.12s`。
- hover 淡入淡出;弹窗 fade + 轻微 zoom;禁弹簧动画。
- 动效只用于澄清状态变化,不装饰屏幕。

## 深浅主题

两端均为浅色默认 + `html[data-theme="dark"]` 深色:

- 所有新增视觉 token **必须**在深色区块给出对应取值,不得只写浅色。
- 浅色是米黄纸感渐变(产品性格,不得改成灰白);深色沿用现有深色板。
- 新 token 命名带语义不带颜色(如 `--shadow-soft`,不是 `--shadow-brown`)。

## 两端同步义务

两端 globals.css 各持有一份相同的设计 token 定义(头部「design tokens」区)。
修改 token 时**必须**两端同步改;扫描脚本会校验两份 token 区一致。

## 扫描脚本与豁免

```bash
python scripts/design_lint.py            # 全扫两端,输出违规清单
python scripts/design_lint.py --check    # 同上,违规时退出码 1(CI 用)
```

规则:字号硬编码、字体栈硬编码、散落圆角、非 var 动效时长、白名单外 box-shadow,
覆盖 `.css` 与 `.tsx`(含内联 style)。

豁免:在违规行上一行或行尾加注释 `/* design-lint:allow 原因 */`(tsx 用 `{/* */}`)。
豁免必须写原因;无因豁免在 review 时打回。
