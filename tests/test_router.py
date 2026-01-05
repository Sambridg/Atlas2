from importlib import util
from pathlib import Path
import sys


def _load_router():
    base = Path(__file__).resolve().parents[1]
    router_path = base / "src" / "caal" / "router.py"
    spec = util.spec_from_file_location("caal_router", router_path)
    module = util.module_from_spec(spec)
    sys.modules["caal_router"] = module
    spec.loader.exec_module(module)
    return module


router = _load_router()

def test_restart_command_requires_confirmation():
    decision = router.decide_route("Please restart the agent now.")
    assert decision["type"] == "command"
    assert decision["command_id"] == "ops.restart_agent"
    assert decision["require_confirm"]


def test_status_command_is_recognized():
    decision = router.decide_route("Can you check the health of the services?")
    assert decision["type"] == "command"
    assert decision["command_id"] == "ops.status"
    assert decision["require_confirm"] is False


def test_voice_command_extracts_topic():
    decision = router.decide_route("Set voice to am_hero")
    assert decision["type"] == "command"
    assert decision["command_id"] == "voice.set_voice"
    assert decision["topic"] == "am_hero"


def test_memory_show_command_detects_bucket():
    decision = router.decide_route("Show memory for project-x")
    assert decision["type"] == "command"
    assert decision["command_id"] == "memory.show_bucket"
    assert decision["topic"] == "project-x"


def test_memory_list_command():
    decision = router.decide_route("List memory buckets")
    assert decision["type"] == "command"
    assert decision["command_id"] == "memory.list_buckets"


def test_research_intent_hits_research_facet():
    decision = router.decide_route("Could you dig deep on this topic?")
    assert decision["type"] == "research"
    assert decision["facet_id"] == "facet.research"


def test_memory_intent_maps_to_memory_facet():
    decision = router.decide_route("Summarize what we talked about.")
    assert decision["type"] == "facet"
    assert decision["facet_id"] == "facet.memory"


def test_fallback_defaults_to_chat():
    decision = router.decide_route("Hello, how are you doing today?")
    assert decision["type"] == "chat"
    assert decision["require_confirm"] is False
