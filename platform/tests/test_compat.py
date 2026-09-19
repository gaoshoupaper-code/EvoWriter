"""兼容判定测试（DEC-003/006）：A-only → compatible；B/C 变化或指纹缺失 → incompatible。"""
from __future__ import annotations

import pytest
from contracts.platform import SurfaceFingerprint

import app.compat as compat
from app.compat import judge_resume


def _fp(a: dict | None = None, b: dict | None = None, c: dict | None = None):
    return SurfaceFingerprint(
        a_text=dict(a or {}), b_param=dict(b or {}), c_code=dict(c or {})
    )


H1, H2, H3 = "h1", "h2", "h3"


def test_identical_fingerprints_compatible():
    old = _fp(a={"prompts/x.md": H1}, b={"p.json": H2}, c={"m.py": H3})
    verdict = judge_resume(old, old)
    assert verdict.decision == "compatible"
    assert verdict.changed_layers == []
    assert verdict.changed_files == []


def test_a_text_only_changes_compatible():
    old = _fp(a={"prompts/x.md": H1})
    # 改 + 增 + 删 都只落在 a_text
    new = _fp(a={"prompts/x.md": H2, "prompts/y.md": H1})
    verdict = judge_resume(old, new)
    assert verdict.decision == "compatible"
    assert verdict.changed_layers == ["a_text"]
    assert sorted(verdict.changed_files) == ["prompts/x.md", "prompts/y.md"]


def test_a_text_deletion_still_compatible():
    old = _fp(a={"prompts/x.md": H1, "skills/s/SKILL.md": H2})
    new = _fp(a={"prompts/x.md": H1})
    verdict = judge_resume(old, new)
    assert verdict.decision == "compatible"
    assert verdict.changed_files == ["skills/s/SKILL.md"]


def test_b_param_change_incompatible():
    old = _fp(b={"params/config.yaml": H1})
    new = _fp(b={"params/config.yaml": H2})  # 修改
    assert judge_resume(old, new).decision == "incompatible"

    new_add = _fp(b={"params/config.yaml": H1, "params/x.json": H2})  # 新增
    assert judge_resume(old, new_add).decision == "incompatible"

    new_del = _fp(b={})  # 删除
    assert judge_resume(old, new_del).decision == "incompatible"


def test_c_code_change_incompatible():
    old = _fp(c={"middleware/m.py": H1})
    new_mod = _fp(c={"middleware/m.py": H2})
    assert judge_resume(old, new_mod).decision == "incompatible"

    new_add = _fp(c={"middleware/m.py": H1, "middleware/new.py": H2})
    assert judge_resume(old, new_add).decision == "incompatible"

    assert judge_resume(old, _fp(c={})).decision == "incompatible"


def test_mixed_a_and_c_change_incompatible():
    old = _fp(a={"prompts/x.md": H1}, c={"m.py": H1})
    new = _fp(a={"prompts/x.md": H2}, c={"m.py": H2})
    verdict = judge_resume(old, new)
    assert verdict.decision == "incompatible"
    assert set(verdict.changed_layers) == {"a_text", "c_code"}


def test_cross_layer_move_reports_both_layers():
    # 同一路径从 b_param 层挪到 c_code 层（跨层移动）→ 两层都算变化，保守拒绝
    old = _fp(b={"x.json": H1})
    new = _fp(c={"x.json": H1})
    verdict = judge_resume(old, new)
    assert verdict.decision == "incompatible"
    assert set(verdict.changed_layers) == {"b_param", "c_code"}


def test_missing_fingerprint_incompatible():
    fp = _fp(a={"prompts/x.md": H1})
    missing_bound = judge_resume(None, fp)
    assert missing_bound.decision == "incompatible"
    assert "绑定版本" in missing_bound.reason

    missing_current = judge_resume(fp, None)
    assert missing_current.decision == "incompatible"
    assert "当前生产版本" in missing_current.reason

    assert judge_resume(None, None).decision == "incompatible"


def test_compare_failure_incompatible(monkeypatch):
    """比对过程抛异常 → 保守 incompatible（不把异常抛给调用方）。"""
    def _boom(_old, _new):
        raise ValueError("diff 引擎故障")

    monkeypatch.setattr(compat, "diff_fingerprints", _boom)
    verdict = judge_resume(_fp(), _fp())
    assert verdict.decision == "incompatible"
    assert "比对异常" in verdict.reason


def test_changed_files_truncated_to_20():
    old = _fp(a={})
    new = _fp(a={f"prompts/{i:02d}.md": H1 for i in range(30)})
    verdict = judge_resume(old, new)
    assert verdict.decision == "compatible"  # 截断不影响判定
    assert len(verdict.changed_files) == compat.MAX_CHANGED_FILES == 20


@pytest.mark.parametrize(
    ("a", "b", "c", "expected_layers"),
    [
        ({"p.md": H1}, {}, {}, ["a_text"]),
        ({}, {"p.json": H1}, {}, ["b_param"]),
        ({}, {}, {"p.py": H1}, ["c_code"]),
    ],
)
def test_diff_layer_attribution(a, b, c, expected_layers):
    layers, files = compat.diff_fingerprints(_fp(), _fp(a, b, c))
    assert [l.value for l in layers] == expected_layers
    assert files == list(a or b or c)
