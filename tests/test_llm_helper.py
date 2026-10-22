import json

from langchain_core.messages import AIMessage
from testzeus_hercules.config import NonSingletonConfigManager
from testzeus_hercules.core.agents import base_nav_agent as base_nav_agent_module
from testzeus_hercules.core.agents import high_level_planner_agent
from testzeus_hercules.core.agents import (
    multimodal_base_nav_agent as multimodal_base_nav_agent_module,
)
from testzeus_hercules.utils.llm_helper import (
    GraphChatResult,
    create_chat_model,
    extract_planner_summary,
)
from testzeus_hercules.utils.model_utils import adapt_llm_params_for_model


def test_create_chat_model_sets_default_timeout_and_retry() -> None:
    model = create_chat_model({"model": "gpt-4o", "api_key": "test-key"}, {})

    assert model.request_timeout == 60.0
    assert model.max_retries == 1


def test_create_chat_model_honors_timeout_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT", "12.5")
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")

    model = create_chat_model({"model": "gpt-4o", "api_key": "test-key"}, {})

    assert model.request_timeout == 12.5
    assert model.max_retries == 0


def test_create_chat_model_strips_autogen_cache_seed() -> None:
    model = create_chat_model({"model": "gpt-4o", "api_key": "test-key"}, {"cache_seed": 42})

    assert "cache_seed" not in model.model_kwargs


def test_adapt_llm_params_strips_autogen_cache_seed() -> None:
    params = adapt_llm_params_for_model("gpt-4o", {"cache_seed": 42})

    assert "cache_seed" not in params


def test_create_chat_model_uses_openai_compatible_claude_params() -> None:
    model = create_chat_model(
        {"model": "claude-haiku-5-5", "api_key": "test-key"},
        {"max_tokens_to_sample": 1200, "temperature": 0.2, "top_p": None},
    )

    assert model.max_tokens == 1200
    assert "max_tokens_to_sample" not in model.model_kwargs
    assert model.temperature == 0.2
    assert "top_p" not in model.model_kwargs


def test_claude_legacy_token_name_normalizes_to_max_tokens() -> None:
    params = adapt_llm_params_for_model("claude-haiku-5-5", {"max_tokens_to_sample": 768})

    assert params["max_tokens"] == 768
    assert "max_tokens_to_sample" not in params


def test_claude_max_tokens_is_kept_and_wins_over_legacy_key() -> None:
    params = adapt_llm_params_for_model("claude-sonnet-4", {"max_tokens": 512, "max_tokens_to_sample": 999})

    assert params["max_tokens"] == 512
    assert "max_tokens_to_sample" not in params


def test_claude_defaults_max_tokens_when_unset() -> None:
    params = adapt_llm_params_for_model("claude-sonnet-4", {})

    assert params["max_tokens"] == 4096
    assert "max_tokens_to_sample" not in params


def test_gpt5_parameter_behavior_is_preserved() -> None:
    params = adapt_llm_params_for_model(
        "gpt-5-mini",
        {"max_tokens": 1024, "temperature": 0, "top_p": None},
    )

    assert params["max_completion_tokens"] == 1024
    assert "temperature" not in params
    assert "top_p" not in params


def test_planner_agent_strips_autogen_cache_seed(monkeypatch) -> None:
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)

    agent = high_level_planner_agent.PlannerAgent({"model": "gpt-4o", "api_key": "test-key"}, {"cache_seed": 42})

    assert "cache_seed" not in agent.llm.model_kwargs


# ── summary fallback-order matrix ─────────────────────────────────────────────


def test_summary_terminal_planner_beats_terminal_helper() -> None:
    terminal_planner = json.dumps({"terminate": "yes", "final_response": "planner verdict", "is_assert": True, "is_passed": True, "assert_summary": "ok"})
    result = GraphChatResult(
        messages=[AIMessage(content=json.dumps({"terminate": "no", "next_step": "keep going"})), AIMessage(content=terminal_planner)],
        terminate="yes",
        last_helper_response="PASS: helper verdict",
    )

    summary = json.loads(result.summary)
    assert summary["terminate"] == "yes"
    assert summary["final_response"] == "planner verdict"


def test_summary_terminal_helper_beats_nonterminal_planner() -> None:
    result = GraphChatResult(
        messages=[AIMessage(content=json.dumps({"terminate": "no", "next_step": "keep going", "target_helper": "browser"}))],
        terminate="yes",
        last_helper_response="FAIL: order total mismatched",
    )

    summary = json.loads(result.summary)
    assert summary["terminate"] == "yes"
    assert summary["is_assert"] is True
    assert summary["is_passed"] is False


def test_summary_nonterminal_planner_beats_generic_completion() -> None:
    result = GraphChatResult(
        messages=[AIMessage(content=json.dumps({"terminate": "no", "next_step": "keep going", "target_helper": "browser"}))],
        terminate="no",
        last_helper_response="plain helper narration without a verdict",
    )

    assert json.loads(result.summary)["next_step"] == "keep going"


def test_summary_generic_completion_only_when_nothing_terminal_exists() -> None:
    result = GraphChatResult(messages=[], terminate="yes", last_helper_response="")

    assert json.loads(result.summary) == {"terminate": "yes", "final_response": "Test ended without planner JSON."}


def test_summary_empty_when_graph_never_terminated() -> None:
    result = GraphChatResult(messages=[], terminate="no", last_helper_response="")

    assert result.summary == ""


# ── planner-JSON detection wins over the non-planner substring heuristic ──────


def test_extract_planner_summary_keeps_terminal_json_mentioning_sync_playwright() -> None:
    # A terminal planner JSON that merely mentions "sync_playwright" is still a
    # planner decision: the substring heuristic must only ever reject content
    # that is not planner-shaped, never discard a verdict.
    terminal = json.dumps(
        {
            "terminate": "yes",
            "is_assert": True,
            "is_passed": True,
            "final_response": "verified the sync_playwright integration",
        }
    )
    messages = [
        AIMessage(content=json.dumps({"terminate": "no", "next_step": "keep going", "target_helper": "browser"})),
        AIMessage(content=terminal),
    ]

    assert extract_planner_summary(messages) == terminal


def test_extract_planner_summary_still_skips_code_block_content() -> None:
    # Non-planner-shaped content (raw code) is still never a summary candidate.
    messages = [
        AIMessage(content="from playwright.sync_api import sync_playwright\n\ndef run_task():\n    pass"),
        AIMessage(content=json.dumps({"terminate": "no", "next_step": "keep going", "target_helper": "browser"})),
    ]

    assert extract_planner_summary(messages) == json.dumps({"terminate": "no", "next_step": "keep going", "target_helper": "browser"})


# ── prompt templates tolerate unknown placeholders ────────────────────────────


def test_base_nav_agent_prompt_keeps_unknown_placeholder(monkeypatch) -> None:
    monkeypatch.setattr(base_nav_agent_module, "get_user_ltm", lambda: "LTM body")
    prompt = "Test information: $basic_test_information\nUnknown: ${not_a_var}"

    agent = base_nav_agent_module.BaseNavAgent({"model": "gpt-4o", "api_key": "test-key"}, {}, None, agent_prompt=prompt)

    assert "LTM body" in agent.system_message
    assert "${not_a_var}" in agent.system_message


def test_multimodal_nav_agent_prompt_keeps_unknown_placeholder(monkeypatch) -> None:
    monkeypatch.setattr(base_nav_agent_module, "get_user_ltm", lambda: "LTM body")
    prompt = "Test information: $basic_test_information\nUnknown: ${not_a_var}"

    agent = multimodal_base_nav_agent_module.MultimodalBaseNavAgent({"model": "gpt-4o", "api_key": "test-key"}, {}, None, agent_prompt=prompt)

    assert "LTM body" in agent.system_message
    assert "${not_a_var}" in agent.system_message


def test_nav_agent_prompt_still_interpolates_ltm(monkeypatch) -> None:
    monkeypatch.setattr(base_nav_agent_module, "get_user_ltm", lambda: "known test data")
    prompt = "Test information: $basic_test_information"

    agent = base_nav_agent_module.BaseNavAgent({"model": "gpt-4o", "api_key": "test-key"}, {}, None, agent_prompt=prompt)

    assert "known test data" in agent.system_message
    assert "$basic_test_information" not in agent.system_message


def test_parse_agent_response_passes_through_failure_records() -> None:
    from testzeus_hercules.utils.llm_helper import parse_agent_response

    record = {"category": "agent_chain_failure", "stage": "scenario_execution", "error_type": "RuntimeError", "message": "boom", "evidence_path": "/tmp/evidence.log"}
    content = json.dumps({"terminate": "yes", "is_assert": True, "is_passed": False, "assert_summary": "aborted", "execution_failure": record, "other_failure": None})

    parsed = parse_agent_response(content)

    assert parsed["execution_failure"] == record  # rendered by the junit failure-record path
    assert "other_failure" not in parsed  # only the known failure record is passed through


def test_parse_agent_response_plain_result_has_no_failure_keys() -> None:
    from testzeus_hercules.utils.llm_helper import parse_agent_response

    parsed = parse_agent_response(json.dumps({"terminate": "yes", "is_assert": True, "is_passed": True, "assert_summary": "ok"}))

    assert "execution_failure" not in parsed


def test_planner_agent_normalizes_claude_params_for_chatopenai(monkeypatch) -> None:
    monkeypatch.setattr(high_level_planner_agent, "get_user_ltm", lambda: None)

    agent = high_level_planner_agent.PlannerAgent(
        {"model": "claude-haiku-5-5", "api_key": "test-key"},
        {"max_tokens_to_sample": 1200, "temperature": 0.1},
    )

    assert agent.llm.max_tokens == 1200
    assert agent.llm.temperature == 0.1
    assert "max_tokens_to_sample" not in agent.llm.model_kwargs


def test_full_config_normalization_uses_openai_compatible_token_key() -> None:
    config = NonSingletonConfigManager(
        {
            "LLM_MODEL_NAME": "claude-haiku-5-5",
            "LLM_MODEL_TEMPERATURE": 0.1,
            "LLM_MODEL_MAX_TOKENS": 1200,
        },
        ignore_env=True,
    )

    params = config.get_adapted_llm_params()

    assert params["max_tokens"] == 1200
    assert "max_tokens_to_sample" not in params
