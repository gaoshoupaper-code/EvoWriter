#!/usr/bin/env python3
"""Run scene acceptance cases against the production writing chain, on the server.

Example (inside the executor container):
    python /tmp/verify-writing-scene.py --output-dir /tmp/scene-check --cases middle,no_match

Uses real model calls (provider tokens are consumed), isolated SQLite, synthetic
authors and books. Never imports app.main or changes a real author's settings.
Results are evidence for editorial review, not an automatic literary-quality grade.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import traceback
import uuid


CASES = (
    "middle", "opening", "no_match", "no_skill", "style_only", "budget_one",
    "budget_four", "budget_missing", "overflow", "resume_before", "resume_after",
    "tail", "long_chapter", "very_long_chapter", "short_scene",
)
NEGOTIATION = (
    "沈砚到启明商行买灯油。贺掌柜报价八十枚银币，沈砚只有六十枚。"
    "沈砚先要求降价，贺掌柜明确拒绝。沈砚发现商行货船被坏水闸堵住，"
    "提出今夜免费修水闸换取降价。贺掌柜因货船不能再耽搁而接受六十枚银币。"
    "沈砚交出铜印作抵押，约定午夜前修好，否则铜印被没收。"
    "报价、拒绝、交换条件、带代价的约定都实际发生；让步原因清楚。"
)
BEFORE = (
    "沈砚先在渡口办理夜航登记，核验旧票和路线，发现船要滞留到天明。"
    "铜印一直收在内袋，身上只有六十枚银币；登记时尚未见到贺掌柜。"
)
AFTER = (
    "沈砚拿到灯油后出门，检查灯芯，沿河堤寻找南门的修闸入口。"
    "他避开塌陷的石阶，核对水位，确认要从闸底入手。"
    "章末钩子：南门门缝里被人塞进一张黑色船票，落款竟是沈砚的名字。"
)
QUIET = (
    "沈砚在渡口登记夜航，核验旧票，查清封航原因。随后他回空仓取工具，"
    "逐项核验损耗，沿河堤寻找南门的修闸入口，避开塌陷石阶并核对水位。"
    "本章没有买卖、讨价还价或谈判，不出现贺掌柜。"
    "章末钩子：南门门缝里出现一张黑色船票，落款竟是沈砚的名字。"
)
TAIL_APPROACH = (
    "沈砚先沿河堤寻找南门修闸入口，避开塌陷石阶，核对石标尺上的水位。"
    "他结合维护图确认闸底尚有落脚位置，但没有耐潮灯油就无法进入暗渠检修，"
    "因此折返启明商行买油。"
)
LONG_CHAPTER = (
    "本章按以下先后经过推进，不跳过原因与处理结果。"
    "沈砚先到渡所登记夜航，得知闸门卡死，旧票只能在今夜使用。"
    "他核验旧票背面的验讫印，发现原登记的南堤路线已经停用。"
    "他逐项对照封航告示与维护图，查清可以经上游栈桥绕行到涵洞入口。"
    "为避免白跑，他回空仓清点铁钳、校准尺、麻绳与油灯，列出随身工具清单。"
    "铁钳的齿口磨损，他从旧备件盒找出替换钳头，装好后夹木块试验咬合。"
    "麻绳中段受潮发霉，他剪去损伤段，重新打绳扣，并用重工具箱试验承重。"
    "校准尺的刻度沾了油污，他擦净刻度，对照木案上的旧基准线核准读数。"
    "油灯试燃时火苗过小，他发现灯芯太短，换用箱中备用灯芯，补入仓内已有灯油并扣紧灯罩。"
    "他按维护图上的作业顺序分装工具，将六十枚银币和铜印藏好，不消费也不抵押。"
    "沿南堤走到断阶时，他用铁钎试探石沿，确认负重通过有坍塌风险，改走已经查清的上游栈桥。"
    "栈桥木板湿滑，他用试验过的绳索固定背囊，降低重心，逐段试稳后通过。"
    "到石标尺前，他发现旧泥痕会误导读数，先擦净标尺，再比对当前水线与维护图。"
    "确认通往涵洞的落脚位置后，他向值更人出示旧票与工具清单，按登记规矩领取南门钥匙。"
    "钥匙进入锁孔却转不动，他检查锈蚀位置，用随身工具和少量灯油处理，再试开锁。"
    "南门打开一线，沈砚从门缝发现一张黑色船票，落款竟是自己的名字，本章到此结束。"
    "全章没有买卖、报价、降价或谈判，不出现贺掌柜，不进入修闸后的情节。"
)
VERY_LONG_CHAPTER = (
    "本章按以下先后经过推进，不跳过原因、判断、处理与验证结果。"
    "用人物实际观察和操作呈现经过，不用一句处理好了概述，也不重复铺陈或增加无关事件凑字。"
    "沈砚先到渡所登记夜航，得知闸门卡死，旧票只能在今夜使用。"
    "他核验旧票背面的验讫印，发现原登记的南堤路线已经停用。"
    "他逐项对照封航告示与维护图，查清可以经上游栈桥绕行到涵洞入口。"
    "维护图一角还叠着一张旧改道纸，箭头却指向已停用的南堤。他不能凭箭头猜路，"
    "先辨两张纸的签发日期与验讫记号，再核对告示列出的封闭区段，确认旧纸已经失效，"
    "把有效路线上的栈桥转角、排水沟与涵洞位置依次记在随身纸上，避免途中再走错。"
    "为避免白跑，他回空仓清点铁钳、校准尺、麻绳与油灯，列出随身工具清单；"
    "铁钎、短柄锤、木楔、针线与备用木板也都来自空仓已有物资，按用途补入清单。"
    "铁钳的齿口磨损，他从旧备件盒找出替换钳头，装好后夹木块试验咬合。"
    "试敲木楔时，短柄锤的锤头向侧面晃动。他先停手，检查木柄与锤眼间的空隙，"
    "从备件盒取出合适的楔片重新锁紧，再对废木连续轻敲并逐渐加力，"
    "确认锤头没有松脱、木柄也没有裂纹，才把短柄锤收进工具包，供途中固定木板使用。"
    "麻绳中段受潮发霉，他剪去损伤段，重新打绳扣，并用重工具箱试验承重。"
    "校准尺的刻度沾了油污，他擦净刻度，对照木案上的旧基准线核准读数。"
    "油灯试燃时火苗过小，他发现灯芯太短，换用箱中备用灯芯，补入仓内已有灯油并扣紧灯罩。"
    "他把亮灯移到仓门风口试用，火苗却渐渐变暗。沈砚先移回背风处，"
    "检查灯芯供油正常后发现灯罩气孔积着烟垢；他熄灯清孔，再扣好灯罩重试，"
    "分别在门外迎风与侧风处停留，确认火焰稳定、没有漏油，才认定油灯能带上栈桥。"
    "他按维护图上的作业顺序分装工具，将六十枚银币和铜印藏好，不消费也不抵押。"
    "满载背囊提起时，底角缝线拉开，铁钳差点坠出。他先卸下重物，找出裂口的旧线，"
    "用仓内的厚帆布、针线补牢受力处，把硬角工具重新包裹分开放好，"
    "再装回原有重量、背起蹲下并走过仓内门槛，核对缝口与肩带没有继续滑裂，才出发。"
    "沿南堤走到断阶时，他用铁钎试探石沿，确认负重通过有坍塌风险，改走已经查清的上游栈桥。"
    "通往栈桥的支沟涨水，原有踏石只露出尖顶。他先用铁钎试深浅，发现贴岸处也有暗坑，"
    "没有负重硬跨，而是按纸上路线沿沟上行，找到两岸较近且石基完整的位置。"
    "他将从空仓带来的备用木板架在两侧石基上，用木楔卡住板端，先压板检查挠曲与滑移，"
    "再解下背囊分次送到对岸，扶住岸边石壁通过，最后取回仍要使用的木板与木楔。"
    "栈桥木板湿滑，他用试验过的绳索固定背囊，降低重心，开始逐段试稳过桥。"
    "行至桥中段，一块跳板在试踩时翘起，露出下方松脱的钉孔。他立即退回已经踏稳的桥段，"
    "检查横梁没有腐空后，将带来的木板横铺在两根完整横梁上，用短柄锤和木楔固定支点，"
    "先用铁钎压试，再以空手探步确认新板没有侧翻，才背起包越过缺口，继续走到栈桥尽头。"
    "到石标尺前，他发现旧泥痕会误导读数，先擦净标尺，再比对当前水线与维护图。"
    "确认通往涵洞的落脚位置后，他向值更人出示旧票与工具清单，按登记规矩办理南门钥匙领用。"
    "领用册上的锁号与钥匙牌被油污盖住，值更人一时拿不准手边两把旧钥匙哪把属于南门。"
    "沈砚没有轮流乱试，先用干布擦净钥匙牌，请值更人对照册中的门位与齿形图，"
    "指出其中一把对应旧副门；双方核清南门锁号后登记领用、收回旧票清单，才携正确钥匙下涵道。"
    "南门前的石道被小块落石堵住，最外侧又贴着急流。他先把背囊放在高处稳台，"
    "用铁钎逐块试探落石下是否还有松动，确认上方没有继续掉落才从内侧分次移开碎石，"
    "将清出的石块放到不会滚入水道的凹处，再空手试走、核对脚下不滑移，最后取回背囊来到门前。"
    "钥匙进入锁孔却转不动，他检查锈蚀位置，用随身工具和少量灯油处理，再试开锁。"
    "南门打开一线，沈砚从门缝发现一张黑色船票，落款竟是自己的名字，本章到此结束。"
    "全章没有买卖、报价、降价或谈判，不出现贺掌柜，不新增具名角色；"
    "途中只处理通行与工具问题，不提前进入闸室修好水闸，不进入修闸后的情节。"
)
SHORT_SCENE_BEFORE = (
    "沈砚先在渡口办理夜航登记，核验旧票和路线，发现坏水闸让船滞留到天明。"
    "登记吏告诉他，启明商行的货船也堵在闸前；若午夜前不能过闸，商行就会误掉卸货约期，"
    "还要多付泊位费。沈砚回想自己曾替商行修过绞盘，与贺掌柜相识，对方也知道他会修水闸。"
    "这些身份、手艺与滞船损失在开场交代清楚，进入交易后不重复解释。"
    "沈砚身上只有六十枚银币，铜印收在内袋；他带着修闸工具，却缺少夜间检修用的灯油。"
    "普通开场写到他走至启明商行门外为止，不在开场提前报价、谈条件或达成约定。"
)
SHORT_SCENE_NEGOTIATION = (
    "沈砚进启明商行买灯油，贺掌柜当面报价八十枚银币。沈砚请求按六十枚出售，贺掌柜明确拒绝。"
    "被拒后，沈砚用上文已知的滞船损失作为交换，提出今夜免费修水闸换取降价；"
    "贺掌柜因货船不能再耽搁而接受六十枚银币。沈砚交付六十枚银币，并取出铜印作抵押；"
    "双方明确约定午夜前修好水闸就归还铜印，否则铜印被没收，贺掌柜随即交付灯油。"
    "报价、拒绝、交换条件、带代价的约定四个过程都以实际对话、动作和反应发生；"
    "这是双方直率的交易，不重复拉扯，不新增试探、加价或别的任务；"
    "双方确认约定并交付灯油后结束本场，不提前写修闸或归还铜印。"
)


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def error_frames(exc: BaseException) -> list[dict]:
    # Stack locations are useful diagnostics without exception messages/locals,
    # which can contain credentials from a failed HTTP request.
    return [{"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)]


def body(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if not line.startswith(("#", "> 状态"))).strip()


def text_of(args: dict) -> str:
    return str(args.get("content") or args.get("replacement") or "")


def scene_evidence(text: str) -> dict:
    """Check actual prose, keeping excerpts so a reader can verify the inference."""
    patterns = {
        "offer": r"(?:八十|80)(?:枚)?(?:银币)?",
        "refusal": r"拒绝|不卖|不行|不成|摇头|不肯|不答应|休想|不降",
        "exchange": r"水闸|修闸",
        "cost": r"铜印",
        "agreed_price": r"(?:六十|60)(?:枚)?(?:银币)?",
    }
    matches = {}
    for name, pattern in patterns.items():
        found = re.search(pattern, text)
        matches[name] = None if found is None else {
            "offset": found.start(),
            "excerpt": text[max(0, found.start() - 45):found.end() + 90],
        }
    return {"all_anchors_present": all(matches.values()), "anchors": matches,
            "note": "正文证据候选；仍须人工核对拒绝、交换因果、约定及代价，不能仅凭词语判通过。"}


def is_injected_cancel(exc: BaseException, marker: str) -> bool:
    if not isinstance(exc, asyncio.CancelledError) and type(exc).__name__ != "NodeCancelledError":
        return False
    pending, seen = [exc], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, asyncio.CancelledError) and str(current) == marker:
            return True
        pending.extend(item for item in (current.__cause__, current.__context__) if item is not None)
    return False


def add_import_paths() -> None:
    root = Path(__file__).resolve().parent.parent
    for candidate in (root, root / "executor", Path.cwd(), Path.cwd() / "executor",
                      Path("/app"), Path("/app/executor")):
        if (candidate / "app/platform").is_dir() or (candidate / "contracts").is_dir():
            sys.path.insert(0, str(candidate))


def isolate_settings(runtime: Path):
    from app.platform.core.settings import get_settings
    from app.platform.agent.binding_client import _resolve_executor_path

    original = get_settings()
    source_known = _resolve_executor_path(original.known_production_path)
    # Secret-bearing model caches stay outside the deliverable evidence directory.
    overrides = {
        "DB_PATH": runtime / "acceptance.db", "WORKSPACE_ROOT": runtime / "workspace",
        "MEMORY_DIR": runtime / "memory", "LLM_CONFIG_CACHE_DIR": runtime / "llm-cache",
        "ARTIFACT_CACHE_DIR": runtime / "artifacts", "BINDING_SPOOL_DIR": runtime / "bindings",
        "KNOWN_PRODUCTION_PATH": runtime / "known-production.json",
        "OUTCOME_BUFFER_PATH": runtime / "outcomes.db", "EVOLUTION_NOTIFY_URL": "",
    }
    if source_known.is_file():
        shutil.copy2(source_known, overrides["KNOWN_PRODUCTION_PATH"])
    for key, value in overrides.items():
        os.environ[key] = str(value)
    get_settings.cache_clear()
    return get_settings()


def seed_book(workspace: Path, case: str) -> dict:
    from contracts.writing_artifacts import WritingState, save_writing_state

    scene = case not in {"no_match", "no_skill", "style_only", "long_chapter", "very_long_chapter"}
    budget = {"budget_one": 1, "budget_four": 4, "short_scene": 4}.get(case, 3)
    target_chars = (
        6000 if case == "very_long_chapter"
        else 4200 if case == "long_chapter"
        else 2400 if case in {"middle", "overflow", "budget_four", "tail"}
        else 1800 if case in {"opening", "budget_missing", "resume_before", "resume_after", "short_scene"}
        else 1200
    )
    plot = QUIET if not scene else (
        ("" if case == "opening" else BEFORE) + NEGOTIATION + AFTER)
    hook = "南门门缝出现黑色船票，落款是沈砚的名字。"
    if case == "tail":
        hook = "贺掌柜交付油罐时，沈砚从油罐夹层发现黑色船票，落款是沈砚的名字。"
        plot = BEFORE + TAIL_APPROACH + NEGOTIATION + f"章末钩子：{hook}本章到此结束，不再返回河堤。"
    elif case == "long_chapter":
        plot = LONG_CHAPTER
    elif case == "very_long_chapter":
        plot = VERY_LONG_CHAPTER
    elif case == "short_scene":
        plot = SHORT_SCENE_BEFORE + SHORT_SCENE_NEGOTIATION + AFTER
    files = {
        "主题.md": "# 主题 · 夜航\n> 状态：定稿\n\n一句话主题：沈砚修闸换取夜航机会。\n核心爽点：靠手艺争取主动。\n",
        "总纲.md": "# 总纲 · 夜航\n> 状态：定稿\n\n## 核心纲\n| 卷 | 小目标 | 地图 | BOSS | 关键节点 |\n|---|---|---|---|---|\n| 一 | 修闸通航 | 渡口 | 无 | 查清黑色船票来历 |\n\n## 伏笔登记表\n暂无。\n",
        "文风.md": f"# 文风 · 夜航\n> 状态：定稿\n\n第三人称限知，贴近沈砚。单章目标{target_chars}字。叙述清楚自然，细节具体。无超能力，无等级数值，无感情线。\n",
        "卷纲/第01卷.md": "# 第一卷 · 夜航\n> 状态：定稿\n\n## 发展阶段\n沈砚修闸通航。\n## 剧情推进表\n本卷章号范围：1-1\n| 章号 | 剧情安排 | 主角想要 | 当前矛盾 | 爽点推进 |\n|---|---|---|---|---|\n| 1 | 修闸前取得工具与灯油 | 今夜通航 | 钱少且时间紧 | 用手艺争取机会 |\n## 爽点质检表\n本章无支配或羞辱情节。\n## 卷末钩子\n黑色船票。\n",
        "章纲/第001章.md": (
            "# 第001章 · 灯下的船票\n> 状态：定稿\n\n"
            f"**概括**：{plot}\n\n**矛盾驱动**：沈砚需要今夜修闸通航，钱与时间均有限。\n"
            "**主角状态**：沈砚清醒克制，会修水闸，随身六十枚银币与一枚铜印。\n"
            "**供给点**：依靠观察和手艺找到出路，不凭空获得物资。\n"
            "**爽点与标签**：小；争取主动；不写支配、调教或羞辱。\n"
            f"**钩子**：{hook}\n"
            "**伏笔操作**：埋下黑色船票，当前章不解释来历。\n"
            f"**开头方案**：{'贺掌柜当面报出灯油价格。' if case == 'opening' else '沈砚办理渡口登记，发现今夜无法开船。'}\n"
            "**修法承接**：无。\n**既有人物**：沈砚、贺掌柜；不新增具名角色。\n"
        ),
    }
    for rel, content in files.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    save_writing_state(workspace, WritingState(
        stage="text", confirmed_stage="text", stage_modes={"text": "auto"}, text_batch_size=1))
    if case == "overflow":
        # A persisted, near-target draft makes the chapter-budget conflict observable.
        observations = [
            "涨水淹了旧刻线", "横梁有一道新裂纹", "绳扣被水泡松", "西岸的路已塌了半边",
            "旧票背面留着昨夜的验讫印", "夜航名册没有漏页", "仓门锁芯仍能转动", "石阶长着湿苔",
            "借来的扳手有缺口", "灯芯尚未剪齐", "系船柱已经松动", "仓内没有可用灯油",
            "河水里夹着碎木", "工具箱底板有些受潮", "登记处只剩一盏灯", "南门的路牌倒在草中",
            "远处传来一声断续的钟响", "封航告示钉在门框上", "夜风从破瓦间钻进来", "旧图上标着泄水口",
        ]
        paragraphs = [
            f"沈砚查到第{index + 1}处时，发现{detail}。他没有急着动手，先把眼前的情况记在旧票空白处，"
            "再和沿路看见的水势核对。今晚剩下的时间不多，贸然试错只会白费力气。"
            "他确认笔记没有沾水，便收好旧票，继续往下一处走。"
            for index, detail in enumerate(observations)
        ]
        prefix = "# 第001章 · 灯下的船票\n> 状态：起草中\n\n" + "\n\n".join(paragraphs)
        target = workspace / "正文/第001章.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(prefix + "\n\n沈砚清点六十枚银币，按住内袋里的铜印，走进启明商行。\n", encoding="utf-8")
    skill = None
    if case not in {"no_skill", "style_only"}:
        allocation = "" if case == "budget_missing" else f"本场景占{budget}批的篇幅。\n"
        skill = ("只在剧情真正进入当面买卖谈判时使用；不凭空增加谈判。\n" + allocation +
                 "写足报价、拒绝、交换条件、附带代价的约定四个过程。"
                 "报价必须来自卖方，拒绝后买方才找到可交换的条件；卖方的让步有具体利益，"
                 "约定明确双方所得与违约代价。用动作、对话和反应呈现，不用结果概述替代过程。")
    elif case == "style_only":
        skill = "全章叙述自然具体，对话符合人物身份。禁用‘命运的齿轮’。本技能没有场景生成任务。"
    if case == "short_scene":
        skill += (
            "占4批只是可浮动的篇幅预算，不必用满，也不靠重复内容凑足。"
            "本场是直率交易：上文已经交代双方身份、沈砚的手艺和商行的滞船损失，场内不重复铺陈。"
            "只兑现报价、明确拒绝、修闸换降价和带午夜期限的铜印约定；必要过程与目标效果完成即止，"
            "不增加支线，也不用一句结果概述代替必要过程。"
        )
    return {"expected_scene": scene, "budget": None if case == "budget_missing" else budget,
            "skill": skill, "target_chars": target_chars, "plot": plot}


async def run_case(case: str, output: Path, runtime: Path, settings, db, *,
                   initial_chapter: str | None = None, first_prompt: str | None = None) -> dict:
    from app.domains.writing.agent import MetaAgentService
    from app.domains.writing.styling.store import CreateTypeStore
    from app.platform.core.db import UserRepository, WritingSkillRepository
    from app.platform.state.thread_store import ThreadStore
    from app.platform.trace.recorder import TraceRecorder
    from app.schemas.screenplay import ScreenplayGenerateRequest
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    case_dir = output / case
    case_dir.mkdir()
    owner = UserRepository(db).create(username=f"scene_{uuid.uuid4().hex}", password=uuid.uuid4().hex)["user_id"]
    store = ThreadStore(db, runtime / "workspace")
    book = store.create_workspace(owner, f"验收-{case}")
    thread = store.create_thread(owner, book.workspace_id, f"验收-{case}")
    workspace = Path(book.workspace_path)
    fixture = seed_book(workspace, case)
    chapter_path = workspace / "正文/第001章.md"
    if initial_chapter is not None:
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        chapter_path.write_text(initial_chapter, encoding="utf-8")
        fixture["seeded_chapter_sha256"] = hashlib.sha256(initial_chapter.encode("utf-8")).hexdigest()
    skills = WritingSkillRepository(db)
    ids = []
    if fixture["skill"]:
        ids.append(skills.create(owner_id=owner, name="验收文风" if case == "style_only" else "验收谈判",
                                 description="隔离验收夹具", content=fixture["skill"], stage="text")["skill_id"])
    skills.set_enabled_for_stage(owner, "text", ids)
    enabled_skill_names = {skill["name"] for skill in skills.list_enabled_for_owner(owner)
                           if skill["stage"] == "text"}
    fixture["enabled_skill_names"] = sorted(enabled_skill_names)
    dump(case_dir / "fixture.json", fixture)
    shutil.copytree(workspace, case_dir / "input")
    writes, reviews, trace_ids, statuses = [], [], [], []
    before_resume = None
    last_saved = chapter_path.read_text(encoding="utf-8") if chapter_path.exists() else ""
    injected = False
    injection = None
    recovery_started = recovery_returned = False

    class Recorder(TraceRecorder):
        def append_event(self, trace_id, values):
            nonlocal injected, injection, last_saved
            event = super().append_event(trace_id, values)
            if trace_id not in trace_ids:
                trace_ids.append(trace_id)
            args = values.get("tool_args") or {}
            chapter_save = (values.get("tool_name") == "save_artifact" and
                            args.get("kind") == "chapter_text")
            valid_scene_request = (chapter_save and args.get("mode") in {"full", "append"}
                                   and args.get("unit_kind") == "skill_scene"
                                   and args.get("skill_name") in enabled_skill_names)
            text = text_of(args)
            saved_scene = False
            if chapter_save and values.get("type") == "tool_end":
                current = chapter_path.read_text(encoding="utf-8") if chapter_path.exists() else ""
                reply = values.get("tool_output") or {}
                reply_text = str(reply.get("content", "")) if isinstance(reply, dict) else str(reply)
                record = {"trace_id": trace_id, "sequence": event.sequence,
                          "call_id": values.get("tool_call_id"), "mode": args.get("mode", "edit"),
                          "unit_kind": args.get("unit_kind"), "skill_name": args.get("skill_name"),
                          "piece": text, "piece_chars": len(body(text)), "saved_chars": len(body(current)),
                          "changed_saved_text": current != last_saved,
                          "save_succeeded": reply_text.startswith(("已落盘", "已追加落盘")),
                          "scene": scene_evidence(text), "tool_output": values.get("tool_output"),
                          "snapshot": f"writes/{len(writes) + 1:03d}.md"}
                writes.append(record)
                last_saved = current
                target = case_dir / record["snapshot"]
                target.parent.mkdir(exist_ok=True)
                target.write_text(current, encoding="utf-8")
                saved_scene = (valid_scene_request and record["save_succeeded"]
                               and record["changed_saved_text"])
            if values.get("type") == "tool_start" and values.get("tool_name") == "task" and args.get("subagent_type") == "review":
                current = chapter_path.read_text(encoding="utf-8") if chapter_path.exists() else ""
                reviews.append({"trace_id": trace_id, "sequence": event.sequence,
                                "writes_before_review": len(writes), "chars": len(body(current)),
                                "hook_present": "黑色船票" in current,
                                "state_lines": [line for line in current.splitlines() if line.startswith("> 状态")],
                                "scene": scene_evidence(current)})
            boundary = "tool_start" if case == "resume_before" else "tool_end"
            if (case.startswith("resume_") and not injected and valid_scene_request
                    and values.get("type") == boundary
                    and (case == "resume_before" or saved_scene)):
                # Fault injection occurs at the real tool boundary, after real model
                # generation. No model/tool result or checkpoint is substituted.
                injected = True
                marker = f"acceptance:{case}:{values.get('tool_call_id')}"
                injection = {"round": round_index + 1, "trace_id": trace_id,
                             "sequence": event.sequence, "event_type": values.get("type"),
                             "call_id": values.get("tool_call_id"), "unit_kind": args.get("unit_kind"),
                             "skill_name": args.get("skill_name"), "mode": args.get("mode"),
                             "saved_scene": saved_scene, "marker": marker, "caught": False}
                dump(case_dir / "injection.json", injection)
                self.flush_sync(trace_id)
                raise asyncio.CancelledError(marker)
            return event

    recorder = Recorder()
    recorder.start_drain()
    checkpoint_path = runtime / f"{case}-checkpoints.db"
    error = None
    failure_frames = []
    rounds = 2 if case.startswith("resume_") else 1
    try:
        for round_index in range(rounds):
            # Reopening the saver and rebuilding the service tests persisted recovery.
            async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
                await saver.setup()
                service = MetaAgentService(settings, runtime / "workspace", recorder,
                                           CreateTypeStore(db, store.workspaces), saver)
                message = "写第1章。" if round_index == 0 else "继续完成第1章。"
                if case == "overflow" and round_index == 0:
                    message = "继续完成第1章现有起草中正文。"
                if round_index == 0 and first_prompt is not None:
                    message = first_prompt
                request = ScreenplayGenerateRequest(thread_id=thread.thread_id, prompt=message)
                with (case_dir / f"round-{round_index + 1}.sse").open("w", encoding="utf-8") as stream:
                    try:
                        async for frame in service.generate_stream(request, thread, owner_id=owner, run_purpose="optimization"):
                            stream.write(frame)
                            stream.flush()
                            if frame.startswith("event: status"):
                                for line in frame.splitlines():
                                    if line.startswith("data: "):
                                        statuses.append(json.loads(line[6:]))
                                        if round_index == 1 and statuses[-1].get("status") == "started":
                                            recovery_started = True
                    except (asyncio.CancelledError, Exception) as exc:
                        if (not injection or injection["round"] != round_index + 1
                                or not is_injected_cancel(exc, injection["marker"])):
                            raise
                        injection.update({"caught": True, "exception_type": type(exc).__name__,
                                          "exception_frames": error_frames(exc)})
                        dump(case_dir / "injection.json", injection)
                    else:
                        if round_index == 1:
                            recovery_returned = True
                if round_index == 0 and case.startswith("resume_"):
                    before_resume = chapter_path.read_text(encoding="utf-8") if chapter_path.exists() else ""
                    (case_dir / "before-resume.md").write_text(before_resume, encoding="utf-8")
                    if not injected:
                        break
    except Exception as exc:
        error = type(exc).__name__
        failure_frames = error_frames(exc)
    finally:
        await recorder.aclose()
    final = chapter_path.read_text(encoding="utf-8") if chapter_path.exists() else ""
    (case_dir / "chapter.md").write_text(final, encoding="utf-8")
    execution_error = error
    error_codes = [error] if error else []
    status_match = re.search(r"(?m)^>\s*状态[：:]\s*([^\r\n]+)", "\n".join(final.splitlines()[:8]))
    chapter_status = status_match.group(1).strip() if status_match else None
    chapter_complete = bool(
        chapter_status and any(value in chapter_status for value in ("初稿", "定稿"))
        and not any(value in chapter_status for value in ("起草中", "进行中", "未完成", "未定稿"))
    )
    # A completed service turn can still leave only an unfinished chapter.
    # This checks persisted status, not prose quality or the trace's completion flag.
    if not chapter_complete:
        error = "chapter_incomplete"
        error_codes.append(error)
    interruption_observed = bool(injection and injection["caught"])
    recovery_observed = recovery_started and recovery_returned
    if case.startswith("resume_") and not interruption_observed:
        error = "interruption_not_observed"
        error_codes.append(error)
    elif case.startswith("resume_") and not recovery_observed:
        error = "recovery_not_observed"
        error_codes.append(error)
    events = []
    for trace_id in trace_ids:
        events.extend(event.model_dump(mode="json") for event in
                      recorder.read_trace_events(trace_id, hydrate_payloads=True) or [])
    dump(case_dir / "events-hydrated.json", events)
    dump(case_dir / "writes.json", writes)
    dump(case_dir / "reviews.json", reviews)
    snapshots = []
    for trace_id in trace_ids:
        run = recorder.find_run_by_trace_id(trace_id, workspace_path=str(workspace))
        if run:
            snapshots.append({"trace_id": trace_id, "status": run.status,
                              "run_snapshot": run.model_dump(mode="json").get("run_snapshot", {})})
    from app.platform.agent.loader import production_commit
    first_review = reviews[0]["writes_before_review"] if reviews else len(writes)
    draft_pieces = [write for write in writes[:first_review]
                    if write["mode"] in {"append", "full"} and write["changed_saved_text"]]
    scene_writes = [write for write in draft_pieces if write["unit_kind"] == "skill_scene"]
    summary = {
        "case": case, "error": error, "error_codes": error_codes,
        "execution_error": execution_error, "error_frames": failure_frames,
        "production_commit": production_commit(),
        "chapter_status": chapter_status, "chapter_complete": chapter_complete,
        "trace_ids": trace_ids, "statuses": statuses, "run_snapshots": snapshots,
        "final_chars": len(body(final)), "target_chars": fixture["target_chars"],
        "exceeded_target_by_ten_percent": len(body(final)) > fixture["target_chars"] * 1.1,
        "save_tool_end_count": len(writes),
        "changed_save_count": sum(write["changed_saved_text"] for write in writes),
        "draft_piece_lengths": [write["piece_chars"] for write in draft_pieces],
        "scene_write_candidates": [{"sequence": write["sequence"], "chars": write["piece_chars"],
                                    "unit_kind": write["unit_kind"], "skill_name": write["skill_name"],
                                    "enabled_name_matches": write["skill_name"] in enabled_skill_names,
                                    "all_anchors_present": write["scene"]["all_anchors_present"]} for write in scene_writes],
        "complete_scene_in_one_piece_observed": bool(scene_writes and scene_writes[0]["scene"]["all_anchors_present"]),
        "final_scene_evidence": scene_evidence(body(final)), "review_calls": reviews,
        "interruption_injected": injected, "interruption_observed": interruption_observed,
        "injection": injection, "recovery_started": recovery_started,
        "recovery_returned": recovery_returned, "recovery_observed": recovery_observed,
        "saved_before_resume_chars": len(body(before_resume or "")),
        "saved_before_resume_sha256": hashlib.sha256((before_resume or "").encode()).hexdigest() if before_resume is not None else None,
        "exact_saved_prefix_preserved": final.startswith(before_resume) if before_resume else None,
        "requires_editorial_review": True,
        "review_instructions": [
            "核对正文中的四个过程与让步原因；词语出现不等于因果成立。",
            "对照 writes.json 与逐次快照，区分失败尝试、起草追加、审稿修订；N 不等于调用次数。",
            "检查场景前后的普通批次、真实触发位置、时间/数字/因果/道具与章末钩子。",
            "恢复用例检查实际已保存内容及重启后的写入；审稿改字可能改变精确前缀，不能只看哈希。",
        ],
    }
    dump(case_dir / "summary.json", summary)
    shutil.copytree(workspace / ".writing", case_dir / "writing-state", dirs_exist_ok=True)
    return summary


async def execute(args, output: Path) -> dict:
    add_import_paths()
    from app.platform.core.db import Database, init_database
    from app.platform.core.security import generate_master_key, load_master_key
    from app.platform.memory.store import MemoryStorePool, init_memory_store_pool

    with tempfile.TemporaryDirectory(prefix="writer-scene-runtime-") as temp:
        runtime = Path(temp)
        settings = isolate_settings(runtime)
        db = Database(runtime / "acceptance.db", load_master_key(generate_master_key()))
        init_database(db)
        memory_pool = MemoryStorePool(runtime / "memory")
        init_memory_store_pool(memory_pool)
        results = []
        try:
            for case in args.cases:
                print(json.dumps({"case": case, "status": "running"}), flush=True)
                result = await run_case(case, output, runtime, settings, db)
                results.append(result)
                dump(output / "summary.json", {"cases": results, "requires_editorial_review": True})
                print(json.dumps({"case": case, "status": "evidence_saved", "error": bool(result["error"]),
                                  "chars": result["final_chars"], "commit": result["production_commit"]}), flush=True)
        finally:
            await memory_pool.aclose_all()
            db.close()
        return {"cases": results, "requires_editorial_review": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cases", default="middle,no_match", help="Comma-separated case names, or all")
    args = parser.parse_args()
    args.cases = list(CASES) if args.cases == "all" else list(dict.fromkeys(args.cases.split(",")))
    unknown = set(args.cases) - set(CASES)
    if unknown:
        parser.error(f"Unknown cases: {', '.join(sorted(unknown))}")
    output = args.output_dir.resolve() / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    print(json.dumps({"output_dir": str(output)}), flush=True)
    try:
        result = asyncio.run(execute(args, output))
        return int(any(case["error"] for case in result["cases"]))
    except Exception as exc:
        # Avoid printing configuration, HTTP headers, or exception text containing credentials.
        dump(output / "fatal.json", {"error_type": type(exc).__name__, "frames": error_frames(exc)})
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
