"""surface 指纹提取测试：三类文件正确归类；registry.json / __pycache__ 排除。"""
from __future__ import annotations

import logging

from contracts.surface_types import SurfaceLayer

from app.surface_scan import classify, scan_surface, sha256_file


def _write(root, rel: str, content: bytes = b"x") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_classify_rules():
    """分类规则单元固化：目录前缀 + 后缀的优先级。"""
    assert classify("prompts/a.md") is SurfaceLayer.A_TEXT
    assert classify("prompts/sub/note.txt") is SurfaceLayer.A_TEXT
    assert classify("skills/s/SKILL.md") is SurfaceLayer.A_TEXT
    assert classify("params/config.json") is SurfaceLayer.B_PARAM
    assert classify("deep/nested/x.yaml") is SurfaceLayer.B_PARAM
    assert classify("a.yml") is SurfaceLayer.B_PARAM
    assert classify("b.toml") is SurfaceLayer.B_PARAM
    assert classify("middleware/m.py") is SurfaceLayer.C_CODE
    assert classify("subagents/factory.py") is SurfaceLayer.C_CODE
    # 根 registry.json 是账本指针，不属于包的 surface —— 排除
    assert classify("registry.json") is None
    # 未分类：无后缀 / 图片 / zip
    assert classify("README") is None
    assert classify("assets/logo.png") is None
    assert classify("bundle.zip") is None


def test_scan_surface_layers_and_hashes(tmp_path):
    """三类文件正确归层，hash = 文件内容 sha256hex。"""
    _write(tmp_path, "prompts/a.md", b"prompt-a")
    _write(tmp_path, "prompts/note.txt", b"note")
    _write(tmp_path, "skills/s/SKILL.md", b"skill")
    _write(tmp_path, "params/config.yaml", b"yaml: 1")
    _write(tmp_path, "params/other.json", b"{}")
    _write(tmp_path, "deep/t.toml", b"[t]")
    _write(tmp_path, "middleware/m.py", b"print(1)")
    _write(tmp_path, "subagents/x.py", b"print(2)")
    _write(tmp_path, "top.py", b"print(3)")

    fp = scan_surface(tmp_path)

    assert set(fp.a_text) == {"prompts/a.md", "prompts/note.txt", "skills/s/SKILL.md"}
    assert set(fp.b_param) == {"params/config.yaml", "params/other.json", "deep/t.toml"}
    assert set(fp.c_code) == {"middleware/m.py", "subagents/x.py", "top.py"}
    assert fp.a_text["prompts/a.md"] == sha256_file(tmp_path / "prompts" / "a.md")
    assert fp.c_code["middleware/m.py"] == sha256_file(tmp_path / "middleware" / "m.py")


def test_scan_excludes_registry_and_pycache(tmp_path, caplog):
    """registry.json 不进任何层；__pycache__（含嵌套）与 .py 不进指纹。"""
    _write(tmp_path, "registry.json", b'{"production": 1}')
    _write(tmp_path, "middleware/__pycache__/m.py", b"stale")
    _write(tmp_path, "middleware/__pycache__/m.pyc", b"stale")
    _write(tmp_path, "__pycache__/root.py", b"stale")
    _write(tmp_path, "middleware/m.py", b"real")

    fp = scan_surface(tmp_path)

    all_paths = set(fp.a_text) | set(fp.b_param) | set(fp.c_code)
    assert all_paths == {"middleware/m.py"}
    for layer_files in (fp.a_text, fp.b_param, fp.c_code):
        assert "registry.json" not in layer_files
        assert not any("__pycache__" in p for p in layer_files)


def test_scan_unclassified_files_logged_as_warning(tmp_path, caplog):
    """未分类文件被忽略且记 warning（不静默丢弃）。"""
    _write(tmp_path, "assets/logo.png", b"png")

    with caplog.at_level(logging.WARNING, logger="platform.surface_scan"):
        scan_surface(tmp_path)

    assert any("assets/logo.png" in rec.message for rec in caplog.records)


def test_scan_is_deterministic(tmp_path):
    """重复扫描结果一致（遍历排序，无随机性）。"""
    _write(tmp_path, "prompts/a.md", b"a")
    _write(tmp_path, "middleware/m.py", b"m")

    assert scan_surface(tmp_path) == scan_surface(tmp_path)
