"""contracts.platform 契约测试。

固化三条关键约束：
1. LLM 快照序列化后不含明文凭据字段（需求风险红线）；
2. surface 指纹的三层层归属判定（兼容门禁的判定基础）；
3. 绑定签发请求与 resume 判定响应的基础形状（三端解序列化的前提）。
"""
from __future__ import annotations

import pytest

from contracts.platform import (
    BindingCreate,
    LlmConfigSnapshot,
    ResumeCheck,
    SurfaceFingerprint,
)


class TestLlmConfigSnapshot:
    def test_model_dump_has_no_plaintext_key_field(self):
        """快照只存配置与 key 引用——任何序列化形态不得出现明文 key 字段。"""
        snap = LlmConfigSnapshot(model="m1", base_url="http://x", api_key_ref="cfg-3")
        dumped = snap.model_dump()
        assert "api_key" not in dumped
        assert "api_key_plain" not in dumped
        assert dumped["api_key_ref"] == "cfg-3"

    def test_model_dump_json_has_no_plaintext_key_field(self):
        snap = LlmConfigSnapshot(model="m", base_url="u", api_key_ref=None)
        import json

        raw = json.loads(snap.model_dump_json())
        assert "api_key" not in raw  # 精确键名；api_key_ref（引用）是允许的


class TestSurfaceFingerprint:
    def test_layer_for_returns_matching_layer(self):
        fp = SurfaceFingerprint(
            a_text={"prompts/a.md": "h1"},
            b_param={"config/params.json": "h2"},
            c_code={"middleware/goal.py": "h3"},
        )
        assert fp.layer_for("prompts/a.md").value == "a_text"
        assert fp.layer_for("config/params.json").value == "b_param"
        assert fp.layer_for("middleware/goal.py").value == "c_code"

    def test_layer_for_unknown_path_returns_none(self):
        fp = SurfaceFingerprint()
        assert fp.layer_for("anything.md") is None


class TestApiShapes:
    def test_binding_create_roundtrip(self):
        req = BindingCreate(trace_id="trace-1", harness_commit="abc123")
        parsed = BindingCreate.model_validate(req.model_dump())
        assert parsed == req

    def test_binding_create_rejects_empty_identifiers(self):
        with pytest.raises(Exception):
            BindingCreate(trace_id="", harness_commit="abc")
        with pytest.raises(Exception):
            BindingCreate(trace_id="t", harness_commit="")

    def test_resume_check_defaults(self):
        rc = ResumeCheck(
            trace_id="t", decision="incompatible", reason="c_code changed",
            bound_commit="a", current_commit="b",
        )
        assert rc.changed_layers == []
        assert rc.changed_files == []
        assert rc.decision == "incompatible"
