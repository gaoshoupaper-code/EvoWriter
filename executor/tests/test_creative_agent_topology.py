from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
AGENTS = ("outline", "volume", "chapter", "text")


@pytest.mark.parametrize("agent", AGENTS)
def test_each_agent_runs_with_its_own_release_history_and_workers(agent):
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    for role in ("executor", "platform", "evolution"):
        service = services[f"{role}-{agent}"]
        assert service["environment"]["WRITING_AGENT_ID"] == agent
        assert service["networks"] == [f"creative-{agent}"]
    executor = services[f"executor-{agent}"]
    evolution = services[f"evolution-{agent}"]
    assert executor["environment"]["PLATFORM_URL"] == f"http://platform-{agent}:7790"
    assert executor["depends_on"]["executor"]["condition"] == "service_healthy"
    assert executor["depends_on"][f"platform-{agent}"]["condition"] == "service_healthy"
    assert evolution["environment"]["EXECUTOR_URL"] == f"http://executor-{agent}:7788"
    assert evolution["environment"]["PLATFORM_URL"] == f"http://platform-{agent}:7790"
    harness = next(v for v in evolution["volumes"] if isinstance(v, dict) and v["target"] == "/app/evolution/harnesses")
    assert harness["source"] == f"harness_{agent}"
    assert harness["volume"]["nocopy"] is True
    assert not any(isinstance(v, str) and v.startswith("executor_data:") for v in evolution["volumes"])
    assert f"runtime_{agent}:/app/executor/checkpoints" in executor["volumes"]
    assert evolution["environment"]["SHARED_MIDDLEWARE_DIR"] == "/app/evolution/shared_middleware"
    assert f"evolution_{agent}:/app/evolution/data" in evolution["volumes"]
