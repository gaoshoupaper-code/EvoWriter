import json

import pytest

from contracts.creative_harness import text_assistants, write_denial


def test_only_mounted_text_helpers_can_be_called(tmp_path):
    actors = [
        {"name": "text", "role": "main", "delegates": ["review", "continuity"]},
        {"name": "review", "role": "sub", "delegated_by": "text"},
        {"name": "continuity", "role": "sub", "delegated_by": "text"},
    ]
    path = tmp_path / "architecture.json"
    path.write_text(json.dumps({"agents": actors}))

    def check(helper):
        return write_denial("text", "task", {"subagent_type": helper}, 1, 1, package_root=tmp_path)

    assert check("continuity") is None
    assert check("review") is None
    assert check("absent")
    actors[0]["delegates"].remove("continuity")
    path.write_text(json.dumps({"agents": actors}))
    with pytest.raises(ValueError):
        check("continuity")


@pytest.mark.parametrize("name,alias", [("chapter", ""), ("h", "volume_qc"), ("h", "review")])
def test_text_cannot_mount_other_creative_identities(name, alias):
    with pytest.raises(ValueError):
        text_assistants([{"name": "text", "role": "main", "delegates": ["review", name]},
                         {"name": "review", "role": "sub", "delegated_by": "text"},
                         {"name": name, "role": "sub", "runtime_name": alias, "delegated_by": "text"}])


@pytest.mark.parametrize("helpers", [[], ["continuity"]])
def test_text_can_evaluate_a_candidate_that_removes_or_replaces_review(helpers):
    actors = [{"name": "text", "role": "main", "delegates": helpers}]
    actors += [{"name": name, "role": "sub", "delegated_by": "text"} for name in helpers]
    assert text_assistants(actors) == frozenset(helpers)
