# Tauri + React + Typescript

This template should help get you started developing with Tauri, React and Typescript in Vite.

## 人物卡

- 作品侧栏的「人物卡」位于总纲下、文风上，空时也保留入口。
- 名单可折叠，主角置顶。点击姓名查看卡片，在预览页删除。
- 删除只把选中的卡片移入回收站，不回退进度，也不改已有纲文。
- 点击侧栏「重做人物卡」进入当前对话，填写人物和重做要求后再发送。
- 补卡期间可点击「返回写作」。主角卡缺失时，先重建或恢复主角卡，再继续依赖主角卡的生成。

## 全局写作技能

- 左下角个人菜单按「主题 → 技能 → 修改密码」排列。
- 点击「技能」打开独立管理弹窗，没有作品时也能配置。
- 总纲、卷纲、章纲、正文四阶段分别管理，每阶段最多启用五条。
- 同账号全部新旧作品共用设置，修改从下一次生成生效。
- 首次使用会合并旧作品已启用技能并去重。超额阶段显示「待选择」，选好零至五条并保存。
- 待选期间只暂停对应阶段的新生成，其他阶段与人物卡重做仍可使用。

## Recommended IDE Setup

- [VS Code](https://code.visualstudio.com/) + [Tauri](https://marketplace.visualstudio.com/items?itemName=tauri-apps.tauri-vscode) + [rust-analyzer](https://marketplace.visualstudio.com/items?itemName=rust-lang.rust-analyzer)
