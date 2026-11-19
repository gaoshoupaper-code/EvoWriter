"""Four independently runnable assignments per creative Harness."""
from contracts.target_tasks import configured_task_type

from app.experiments.schemas import PackageCreate

SUITE = "isolated-writing-v1"

# Each story appears at four distinct levels. Its test inputs are materialized
# inside the individual package, so no live output from another writer is needed.
_STORIES = [
    ("潮线邮局", "辛岚", "海上邮差", "退潮前送达最后一袋信",
     "航道即将封闭。灯塔员把最后一名收件人划进了撤离名单",
     "辛岚用自己的船票换来一次渡船，决定留下等回执",
     "船舱口的碎冰刮掉了信袋上的旧邮戳", "收件人回赠的空白回执"),
    ("停钟夜班", "贺珂", "车站机修工", "让停摆的末班车准点疏散病人",
     "备用电只够启动一次。站长坚持先试空车",
     "贺珂把试车窗口让给病人，自己留下手摇道岔",
     "控制台的旧灯偶尔闪亮，时刻表却一直停在零点", "签有三种笔迹的交班单"),
    ("山谷借声", "姚杉", "口述史采集员", "让封村前失语的老人留下证词",
     "村干部只肯留下官样记录，唯一录音带已被误擦",
     "姚杉请三位证人分别敲出遇险当晚的铃声，交由老人指认",
     "木廊还留着旧绳索的凹痕，风一过就发出低鸣", "老人画下的七段停顿"),
    ("无字戏票", "符青", "皮影戏班学徒", "在拆台前完成一场无声演出",
     "戏师突然失聪。最后一幕的机关藏在即将拆掉的台下",
     "符青撤掉说书席，请师父亲手牵最后一根影线",
     "幕后那枚掉瓷的铜扣撞在幕布支杆上", "褪色戏票上用针戳出的座次"),
]


def get_cases():
    kind = configured_task_type()
    records = []
    for index, (title, hero, job, aim, obstacle, decision, detail, item) in enumerate(_STORIES):
        purpose = "optimization" if index < 2 else "regression"
        chapter = (1, 5, 21, 40)[index]
        volume = (chapter - 1) // 20 + 1
        brief = f"创作《{title}》。{job}{hero}要{aim}。{obstacle}。结局必须依靠人物选择，不能靠偶然救援。"
        master = (f"# 总纲 · {title}\n> 状态：草稿\n## 主题\n承担代价，才能使承诺可信。\n"
                  f"## 人物目标与冲突\n{hero}要{aim}。{obstacle}。\n## 主线与分卷\n"
                  f"第一卷发现限制，卷末拿到{item}；第二卷为选择付出代价，结尾{decision}。")
        volume_plan = (f"# 卷纲 · 第{volume}卷\n> 状态：草稿\n本卷章号范围：{(volume - 1)*20+1}-{volume*20}\n"
                       f"本卷冲突：{obstacle}。\n第{chapter}章：{detail}，主角根据{item}作出选择。"
                       f"本章只改变行动条件，不能提前写出全书结局。")
        card = (f"# 第{chapter}章章纲\n> 状态：草稿\n主角{hero}要{aim}。开场呈现{detail}。"
                f"通过近景动作呈现一次选择：是否交出{item}。障碍是{obstacle}。"
                "结尾明确写出失去的机会，不提前解除所有障碍。")
        instructions = {
            "master_outline": "只交付含主题的总纲，写清目标、冲突、转折与各卷任务。",
            "volume_outlines": f"仅写第{volume}卷卷纲并做卷纲质检，安排本卷二十章的逐章推进。",
            "chapter_outlines": f"写第{chapter}章章纲，写清冲突、状态变化及可供执笔的安排。",
            "chapter_text": f"按章纲创作第{chapter}章正文，实际审稿，按需修订。",
        }
        targets = {"task_type": kind}
        files = {}
        if kind in {"volume_outlines", "chapter_outlines"}:
            targets["volume_ids"] = [volume]
            files["总纲.md"] = master
        if kind == "volume_outlines":
            targets["chapters_per_volume"] = 20
        if kind in {"chapter_outlines", "chapter_text"}:
            targets["chapter"] = chapter
        if kind == "chapter_outlines":
            files[f"卷纲/第{volume:02d}卷.md"] = volume_plan
        if kind == "chapter_text":
            files[f"章纲/第{chapter:03d}章.md"] = card
        package = PackageCreate.model_validate({
            "title": f"{title} · {instructions[kind].split('，')[0].rstrip('。')}", "purpose": purpose,
            "target": targets, "input": {"demand_md": "" if kind == "chapter_text" else brief + "\n" + instructions[kind],
                                       "files": files, "confirmation_rules": "按给定资料自行完成指定任务。"},
        })
        records.append({**package.model_dump(mode="json"), "id": f"{SUITE}-{kind}-{index+1}",
                        "suite": SUITE, "task_type": kind, "flow_version": 3,
                        "input_revision": package.input.input_revision, "created_at": "2026-11-19T00:00:00+08:00"})
    return records
