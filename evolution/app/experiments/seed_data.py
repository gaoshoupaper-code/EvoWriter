"""Complete, fixed trial inputs. Seeding is additive and never edits existing rows."""
from __future__ import annotations

from app.experiments import repo
from app.experiments.schemas import PackageCreate

SUITE = "stage-smoke-v1"

# Each task gets its own story. Validation and regression never reuse training inputs.
_STORIES = [
    ("铜炉公契", "陆砚", "炉匠", "赤炉城", "矿契被换", "旧炉样上的熔纹", "矿契铜版", "谢芷", "魏稷", "辨纹会灼伤指尖", "取回父亲被夺的炉坊"),
    ("潮城听汛", "沈汀", "测潮员", "澜浦", "堤闸假账", "铜闸上的潮痕", "原始测潮图", "陈澈", "梁湛", "只能辨认最近一日的潮纹", "保住下游住户"),
    ("星港废证", "宁序", "维修学徒", "回环港", "救援记录被抹", "损毁航标里的回波", "黑匣记忆片", "程岚", "傅峤", "回放一次耗尽一枚能源片", "洗清母亲的事故责任"),
    ("长街验粮", "顾穗", "仓吏", "栖川", "赈粮秤被调", "仓底不同的霉痕", "三仓旧账册", "许蘅", "齐檀", "一次只能追查一仓原料", "让灾民领回被扣粮款"),
    ("纸灯辨诏", "祁安", "刻版学徒", "白桥", "封市文告伪造", "错刻的避讳字", "初版木模", "温禾", "邵昶", "只能复印手掌大小的纹路", "阻止师父替人顶罪"),
    ("雪渡回声", "林澈", "索桥修复匠", "霜渡", "断桥嫁祸", "主索的锯痕", "桥索号册", "苏棠", "周隼", "听裂一次会耳鸣一刻钟", "让山民平安过桥"),
    ("盐路换契", "唐栖", "盐行记账人", "鹭城", "假引收税", "盐引印边的缺口", "官盐旧引簿", "叶砂", "韩驰", "比对过原件才能识别赝本", "保住小盐户的生计"),
    ("夜城量星", "许临", "街巷星测师", "雾京", "移星侵地", "屋脊星盘的偏角", "旧城测绘尺", "姜弦", "罗启", "阴天无法使用星尺", "夺回被圈走的祖宅"),
    ("断尺归匣", "林砚", "衡器修理匠", "青衡", "假尺吞工钱", "铜尺端头的磨痕", "工坊原始尺谱", "陈禾", "魏铎", "一次校准只能持续半刻", "让同工拿回应得工钱"),
    ("雾海验印", "闻汐", "船印刻工", "临雾", "伪印扣船", "封泥里的贝屑", "旧船籍印谱", "蓝笙", "邱岳", "接触原印后会短暂失去指尖触觉", "救出被扣船的兄长"),
    ("灯市补月", "阮舟", "阵灯匠", "镜灯城", "假阵吞供奉", "旧灯芯里的逆流纹", "阵灯维护册", "沈璃", "宗越", "连续补阵会灼伤经脉", "让旧街重新点亮公共阵灯"),
    ("山河校书", "梁序", "方志校勘师", "墨岭", "改志吞田", "乡志缺页的针孔", "原始地界拓片", "苏宁", "袁恪", "只能辨纸龄不能读出失字", "保住村人的地契"),
]
_FIELDS = ("title", "hero", "job", "city", "crime", "clue", "proof", "ally", "rival", "limit", "want")
_KINDS = ("volume_outlines", "chapter_outlines", "chapter_text")
_PURPOSES = ("optimization", "optimization", "validation", "regression")
_LABELS = {"volume_outlines": "整本卷纲", "chapter_outlines": "单章章纲", "chapter_text": "单章正文"}
_CHAPTERS = (1, 25, 43, 20)


def _beat(story, chapter):
    h, a, r = story["hero"], story["ally"], story["rival"]
    clue, proof, crime = story["clue"], story["proof"], story["crime"]
    volume, index = (chapter - 1) // 20 + 1, (chapter - 1) % 20
    arena = ("旧坊的公开验收", "行会的资格复核", "全城的最终公议")[volume - 1]
    beats = [
        (f"{h}因{crime}被拒收申诉→从现场看出{clue}，决定保存原件", "留下证据", "执事要求先认罪才准申诉"),
        (f"因此{h}花掉备用钱买回原件→发现编号与{proof}不符", "核实编号", "旧件商只收现钱且不肯作证"),
        (f"但是{r}派人先行封存旧件→{h}用日常工具保住一份痕迹", "保住痕迹", "原件即将被以报废名义销毁"),
        (f"因此{h}申请{arena}的复核→{r}援引旧规拒绝，{a}指出可用的申诉条款", "取得复核资格", "没有正式身份不能递交证据"),
        (f"所以{h}按条款公开演示辨识方法→获得一次限时比对机会", "争取比对", "旁观者认为低阶手艺没有证明力"),
        (f"但是新送来的样品已经被调换→{h}从{clue}发现封签不连续", "确认调包", "若指控失实就永久取消资格"),
        (f"因此{h}请{a}同时记录交接顺序→找出一段无人负责的空档", "固定交接记录", "交接人互相推责"),
        (f"所以{h}用自己的工具作对照→证明异常发生在交接前", "缩小责任范围", "自用工具也可能被说成预先做假"),
        (f"但是{r}扣住{a}的担保文书→{h}放弃便捷通道换回文书", "保护担保人", "继续追查会连累同伴的生计"),
        (f"因此{h}转查独立留底→在{proof}背面找到第二份编号", "找到独立留底", "留底只准在现场查阅，不能带走"),
        (f"所以{h}当场做可复验的记号→让第三方确认编号确曾存在", "取得见证", "记忆或口述不足以构成证据"),
        (f"但是使用手艺触发代价：{story['limit']}→{h}暂停施术，改用普通测量", "保住检验能力", "继续强行使用会失去复核机会"),
        (f"因此{a}按记录分担普通测量→两人重现同一异常", "复现异常", "对手声称只有主角本人才能看到痕迹"),
        (f"所以{h}把复现方法交给现场见证人→异常第一次被公开确认", "公开验证方法", "公开方法也会被对手用于伪造"),
        (f"但是{r}拿出一份更早的假留底→原有证据的先后顺序被质疑", "辨别留底先后", "两份记录表面同样完整"),
        (f"因此{h}对照使用磨损与{clue}→证明假留底晚于事发日", "排除伪留底", "不能把猜测当作时间证据"),
        (f"所以{h}要求双方同时开封留样→{r}被迫选定不可撤回的样品", "锁定对方主张", "对手不断换样规避正面对质"),
        (f"但是开封前有人破坏封存匣→{h}用此前的记号认出替换物", "识别最后调包", "封存损坏可能让全部证据作废"),
        (f"因此{h}依次展示编号、交接与复现实验→{r}无法再把{crime}推给别人", "闭合证据链", "单独一项痕迹都不足以追责"),
        (f"所以{h}在{arena}中赢得本轮裁定→兑现本卷承诺，并固定下一步责任范围", "兑现具体权益", "必须把胜利变成可执行的裁定"),
    ]
    plot, want, conflict = beats[index]
    return {"plot": plot, "want": want, "conflict": conflict,
            "payoff": "【爽】手艺破局：用可复验的实物痕迹反击轻视，证据或资格发生实际变化"}


def _theme(s):
    return f"""# 主题 · {s['title']}
> 状态：定稿
## 一句话主题
被看轻的{s['job']}{s['hero']}以可复验的手艺追查{s['crime']}，最终{s['want']}。
## 核心爽点
手艺破局：用具体操作和公开验证，拆穿掌权者的造假；胜利须落实到证据、资格或权益。
## 核心矛盾
{s['rival']}掌握检验与解释规则，{s['hero']}有实际技术但缺少正式身份。
## 核心爽点剧情
被拒绝→寻找实物痕迹→付出代价复验→公开对质→取得可执行的改变。
## 能力与代价
主角能辨识{s['clue']}；限制为{s['limit']}。不能凭空读取秘密或制造原件。
升级链为识别单件、复验成组、还原交接、公开建立验证规则；升级须有练习和代价。
## 故事边界
三卷，每卷二十章，总计六十章。以{s['city']}为舞台；无感情线，不新增无关支线。
"""


def _master(s, chapter):
    return f"""# 总纲 · {s['title']}
> 状态：定稿
## 核心纲
| 卷 | 主角想要 | 本卷矛盾 | 地图 | BOSS | 卷末结果 |
|---|---|---|---|---|---|
| 1 | 保住申诉原件 | 旧坊拒收与毁证 | {s['city']}旧坊 | {s['rival']}控制的验收方 | 赢得留样资格，查到行会责任 |
| 2 | 取得独立复核权 | 行会用身份与假账封口 | {s['city']}行会 | {s['rival']} | 方法可公开复验，案卷送至公议 |
| 3 | {s['want']} | 权威用伪留底逃避责任 | {s['city']}公议场 | {s['rival']} | 公开奖惩并执行权益裁定 |
## 主线与升压
三卷分别覆盖第1–20、21–40、41–60章。先保原件，再夺复核权，最后执行裁定。
每卷不能重置已有证据或人物状态；对手的程序性阻碍逐卷升级。
## 伏笔登记表
| ID | 伏笔内容 | 埋设章 | 计划回收章 | 状态 |
|---|---|---|---|---|
| F1 | {s['proof']}封签上有一次换匣留下的折痕 | 1 | 20 | {'已收' if chapter > 20 else '待收'} |
| F2 | {s['rival']}在三处留底使用相同的私章缺口 | 3 | 60 | 待收 |
"""


def _volume(s, volume):
    first, last = (volume - 1) * 20 + 1, volume * 20
    rows = []
    for chapter in range(first, last + 1):
        beat = _beat(s, chapter)
        rows.append(f"| {chapter} | {beat['plot']} | {beat['want']} | 人际：{beat['conflict']} | {beat['payoff']} | 主动选择承担验证代价 |")
    hook = ("带着留样资格前往行会复核", "带着独立案卷走进最终公议", f"兑现{s['want']}，结清主要责任")[volume - 1]
    return f"""# 卷纲 · 第{volume:02d}卷 · {s['title']}
> 状态：定稿
## 本卷定位
目标、矛盾、舞台和对手以总纲第{volume}卷为准。不新增目标卷或修改每卷二十章范围。
## 本卷人物定位
{s['hero']}从相信权威认可，转向用可重复的证据争取规则；选择亲自承担公开复核的代价。
{s['ally']}负责独立记录；{s['rival']}利用资格与留底程序阻碍。只使用已有具名人物。
## 剧情推进表
本卷章号范围：{first}-{last}
| 章号 | 剧情安排 | 主角想要 | 当前矛盾 | 爽点推进 | 主角选择 |
|---|---|---|---|---|---|
""" + "\n".join(rows) + f"\n## 质检报告\n因果、驱动和供给逐章对应；关键证据需独立复验。修法指令：无。\n## 卷末钩子\n{hook}。\n"


def _style(s):
    return f"""# 文风 · {s['title']}
> 状态：定稿
## 约束级：风格卡
| 项 | 约定 |
|---|---|
| 人称视角 | 第三人称贴近{s['hero']}，不进入对手内心 |
| 单章字数 | 2000–3000字；以章纲事件完整为准 |
| 节奏 | 用动作推进取证与对质，短句短段，具体说明代价 |
| 本书手艺质感 | 用触摸、比对、测量呈现{s['clue']}，不靠形容词宣布技术高明 |
| 本书对话特征 | 主角先报可验证事实，{s['rival']}先谈资格与规矩；避免旁白替人物争论 |
| 禁忌 | 不新增具名人物，不凭空获知秘密，不重复解释同一规则 |
"""


def _characters(s):
    return {
        "人物/主角.md": f"# 人物卡 · 主角 · {s['hero']}\n> 状态：定稿\n"
        f"身份：{s['city']}的{s['job']}，因{s['crime']}失去正式资格。\n"
        f"Want：{s['want']}。\nNeed：用可复验的证据建立自己的判断。\n"
        "Lie：只要获得权威承认，事实就会被公平对待。\n弧光主型：成长弧。\n"
        f"说话特征：先报测量结果，再问具体责任；示例：‘编号在这里，谁签收的？’\n"
        f"能力：辨识{s['clue']}。边界：{s['limit']}。\n"
        "## 章节轨迹\n前章变化按固定章纲及前文核对，不虚构此前经历。",
        f"人物/{s['ally']}.md": f"# 人物卡 · 重要配角 · {s['ally']}\n> 状态：定稿\n"
        f"身份：与{s['hero']}合作的记录员。性格：谨慎。\n"
        "说话特征：先复述时间和交接人，再签名；示例：‘我只记录亲眼看见的。’\n"
        "关系：起初不愿担保，见到可复验方法后提供独立见证。\n弧光：从自保到承担作证后果。",
        f"人物/{s['rival']}.md": f"# 人物卡 · BOSS · {s['rival']}\n> 状态：定稿\n"
        f"身份：掌握{s['city']}复核权的负责人。挡道理由：掩盖{s['crime']}带来的利益。\n"
        "强处：能决定资格与留底流程。败处：证据必须经过多人交接，无法同时控制所有副本。\n"
        "共情点：害怕追责摧毁自己维持多年的秩序。越线：毁证并牵连担保人。\n"
        "说话特征：把具体事实变成资格争论；示例：‘没有登记，谁能承认你的样品？’\n"
        "弧光：越想维护权威，越依赖伪造，最后被自己设定的复核规则约束。",
    }


def _card(s, chapter):
    beat = _beat(s, chapter)
    opening = _beat(s, chapter - 1)["plot"].split("→")[-1] if chapter > 1 else "申诉窗口即将关闭，主角必须保住原件"
    result = f"""# 章纲 · 第{chapter:03d}章 · {beat['want']}
> 状态：定稿
**出场人物**：{s['hero']}（主角）、{s['ally']}（重要配角）、{s['rival']}（BOSS）。
**人物提示**：{s['hero']}先报事实再问责任；{s['ally']}只记录亲见；{s['rival']}先谈资格。按在册卡，不增新人。
**章节概括**：{beat['plot']}。本章不提前取得后续章节的最终裁定。
**矛盾驱动**：人际；{s['hero']}要{beat['want']}，但{beat['conflict']}。
**主角状态**：开章承接“{opening}”；已有原件和已取得的记录继续有效。章末为“{beat['plot'].split('→')[-1]}”。
**供给点**：【爽】手艺破局｜小｜以实物和可复验操作推进本章事件｜证据或资格按章节概括变化｜偿付本章受到的程序性压制，不提前结清全书矛盾。
**章末钩子**：本章变化引出下一次复核的具体阻碍；钩子对齐所属卷推进表，不另开支线。
**开头方案**：{'前500字写出主角、申诉现场及即将失去原件的危机' if chapter <= 3 else '承接上章钩子：' + opening}。
**修法承接**：无。
**伏笔操作**：{'埋F1：保留封签折痕；不解释全部原因' if chapter == 1 else '无'}。
## 总检记录
事件、冲突、状态和供给对应同一章；已有证据不重置；出场人物均有卡。
"""
    if chapter == 1:
        result += "**书名兑现路径**：第一章展示关键实物，第二章核对编号，第三章完成首次有代价的辨识。\n"
    return result


def _previous_text(s, chapter):
    beat = _beat(s, chapter)
    return f"""# 第{chapter:03d}章 · {beat['want']}
> 状态：定稿
{s['hero']}把包好的{s['proof']}放在桌角，没有松开压着封签的手。窗外的人还在等消息，守门人已两次催他离开。他来这里不是为了争一句好听的话。{s['want']}，需要一份真正能执行的裁定。

{s['ally']}展开交接记录，把到场时间、经手人和匣上的记号分别写在三行。墨迹干透以后，记录员才把纸推到两人中间。“先对事实，后谈是谁的责任。”他说。{s['hero']}点头，将原件朝见证的位置转了半圈。

这一步有清楚的来由：{beat['plot'].split('→')[0]}。主角不能跳过已经约定的程序，也不能再靠对手口头答应。他用一件寻常工具量出原件的边缘，把数值读给记录员，要求记录员先复述，再落笔。

{s['rival']}没有立即看数值。“谁给你的资格？”他先问。{s['hero']}没有同他争身份，只指着记录上已经签过名的空格：“先回答哪一项不对。若要换样，交接人和时间也一起改。”围观者顺着手指看去，第一次发现几份留底并不完全一致。

辨识不是无所不能。{s['limit']}，这条限制从来没有消失。{s['hero']}在代价继续加重前停手，让{s['ally']}按同样的位置做普通比对。方法若只能由一个人说对，就不足以带进公开复核。他宁可慢一步，也不能把猜测写成结论。

记录员报出第二次数值。两次测量能够互相核对，但还不能证明全部责任。{s['hero']}把{s['clue']}的位置圈出，留下复验所需的距离和顺序；原件重新封好，封签上的折痕也一并登记。这份证据只推进眼前的问题，不等于所有争议都已结束。

当场争执终于落到具体变化：{beat['plot'].split('→')[-1]}。{s['ally']}把变化写入记录，注明尚未确认的部分。{s['rival']}拿回自己的留底，没有撤销已确认的事实，却要求下一步严格按另一项条款办理。

{s['hero']}收起原件，核过随身工具和剩余费用。门后的脚步声近了。接下来必须在新的阻碍前保住刚取得的结果；已失去的便捷通道不能凭空回来，尚未拿到的最终裁定也不能被一句话提前领走。他看向记录员，示意带上那份有双方签名的纸。
"""


def get_builtin_packages():
    records = []
    for index, values in enumerate(_STORIES):
        s = dict(zip(_FIELDS, values))
        kind, position = _KINDS[index // 4], index % 4
        purpose, chapter = _PURPOSES[position], _CHAPTERS[position]
        demand = (f"创作《{s['title']}》：{s['city']}的{s['job']}{s['hero']}追查{s['crime']}，目标是{s['want']}。\n"
                  f"核心能力是辨识{s['clue']}，限制为{s['limit']}；关键实物为{s['proof']}。\n"
                  f"同伴{s['ally']}负责独立记录，对手{s['rival']}掌握程序与资格。\n"
                  "完整需求已经给定：全书三卷，每卷二十章，分别覆盖1–20、21–40、41–60章。\n"
                  "核心爽点为手艺破局：每次反击须有具体操作、可验证证据和实际权益变化。\n"
                  "从旧坊申诉到行会复核，再到最终公议；卷卷提高责任范围与失败代价。\n"
                  "第三人称近距离，行动和对话清楚；不新增感情线或无关支线。\n")
        if kind == "volume_outlines":
            target = {"task_type": kind, "volume_ids": [1, 2, 3], "chapters_per_volume": 20}
            files = {}
            demand += "本次生成主题、总纲和整本三卷卷纲，卷纲内逐章列好规划；不生成章纲或正文。"
        else:
            volume = (chapter - 1) // 20 + 1
            target = {"task_type": kind, "volume_ids": [volume], "chapter": chapter}
            files = {"主题.md": _theme(s), "总纲.md": _master(s, chapter), "文风.md": _style(s),
                     f"卷纲/第{volume:02d}卷.md": _volume(s, volume), **_characters(s)}
            if chapter > 1:
                files[f"章纲/第{chapter - 1:03d}章.md"] = _card(s, chapter - 1)
                files[f"正文/第{chapter - 1:03d}章.md"] = _previous_text(s, chapter - 1)
            if kind == "chapter_text":
                files[f"章纲/第{chapter:03d}章.md"] = _card(s, chapter)
                demand += f"本次仅完成第{chapter}章正文、实际审稿及必要修订，不等待人工精修。"
            else:
                demand += f"本次仅生成第{chapter}章章纲指令卡；所属卷是输入，不生成其他章纲或正文。"
        payload = PackageCreate.model_validate({"title": f"{s['title']} · {_LABELS[kind]}", "purpose": purpose,
            "target": target, "input": {"demand_md": demand, "files": files, "skills": [],
                "confirmation_rules": "按完整需求及固定资料自行确认普通创作取舍，不等待作者；保持三卷各二十章及指定章号，不扩展任务；不跳过保存、质检、审稿和必要修订。"}})
        record = payload.model_dump(mode="json")
        record.update(id=f"{SUITE}-{kind}-{position + 1}", suite=SUITE, task_type=kind,
                      input_revision=payload.input.input_revision, created_at="2026-11-07T10:05:00+00:00")
        records.append(record)
    return records


def seed_packages():
    from app.experiments.packages import validate_package
    records = []
    with repo.run_lock(SUITE):
        for package in get_builtin_packages():
            existing = repo.get_package(package["id"])
            if existing is None:
                payload = {key: package[key] for key in ("title", "purpose", "target", "input")}
                validate_package(PackageCreate.model_validate(payload))
                repo.insert("self_evolution_packages", package)
            records.append(existing or package)
    return records
