from testzeus_hercules.core.simple_hercules import SimpleHercules


def test_bulk_enter_text_args_are_normalized() -> None:
    hercules = SimpleHercules("smoke")

    normalized = hercules._normalize_tool_args(
        "bulk_enter_text",
        {
            "entries": [
                {"element_md": "44", "text": "hello", "press_enter": True},
                {"selector": "[md='68']", "value_to_fill": "world"},
            ]
        },
    )

    assert normalized == {"entries": [["44", "hello"], ["[md='68']", "world"]]}


def test_bulk_enter_text_list_shape_is_normalized() -> None:
    hercules = SimpleHercules("smoke")

    normalized = hercules._normalize_tool_args(
        "bulk_enter_text",
        [
            {"md": "44", "text": "hello"},
            ("[md='68']", "world"),
        ],
    )

    assert normalized == {"entries": [["44", "hello"], ["[md='68']", "world"]]}


def test_bulk_enter_text_texts_alias_is_normalized() -> None:
    hercules = SimpleHercules("smoke")

    normalized = hercules._normalize_tool_args(
        "bulk_enter_text",
        {
            "texts": [
                {"element_id": "44", "value_to_fill": "hello"},
                {"id": "[md='68']", "text": "world"},
            ]
        },
    )

    assert normalized == {"entries": [["44", "hello"], ["[md='68']", "world"]]}


def test_press_key_combination_keys_alias_is_normalized() -> None:
    hercules = SimpleHercules("smoke")

    normalized = hercules._normalize_tool_args(
        "press_key_combination",
        {"keys": ["Control", "Tab"]},
    )

    assert normalized == {"key_combination": "Control+Tab"}


def test_press_key_combination_string_alias_is_normalized() -> None:
    hercules = SimpleHercules("smoke")

    normalized = hercules._normalize_tool_args(
        "press_key_combination",
        {"keys": "Enter"},
    )

    assert normalized == {"key_combination": "Enter"}


def test_force_new_tab_is_enabled_for_multi_tab_intent() -> None:
    hercules = SimpleHercules("smoke")
    state = {
        "task": "Open https://example.com and compare it with https://example.org in another tab",
        "evidence": [{"tool": "open_url", "url": "https://example.com/"}],
    }
    normalized_args = {"url": "https://example.org"}

    assert hercules._should_force_new_tab(state, "open_url", normalized_args)


def test_fallback_planner_opens_next_url_in_new_tab() -> None:
    hercules = SimpleHercules("smoke")

    decision = hercules._fallback_planner_decision(
        {
            "task": "Open https://example.com and compare it with https://example.org in another tab",
            "evidence": [{"tool": "open_url", "url": "https://example.com/"}],
        }
    )

    assert decision["mode"] == "execute"
    assert decision["next_action"] == {
        "tool": "open_url",
        "args": {"url": "https://example.org", "force_new_tab": True},
    }


def test_plan_steps_are_deduplicated_and_completion_is_preserved() -> None:
    hercules = SimpleHercules("smoke")

    normalized = hercules._normalize_plan(
        [
            {"step": "Navigate to target page", "status": "pending"},
            {"step": "Navigate to target page", "status": "completed"},
            {"step": "Collect compact browser evidence", "status": "pending"},
        ]
    )

    assert normalized == [
        {"step": "Navigate to target page", "status": "completed"},
        {"step": "Collect compact browser evidence", "status": "pending"},
    ]


def test_action_signature_normalizes_open_url_and_ignores_timeout() -> None:
    hercules = SimpleHercules("smoke")

    first = hercules._action_signature(
        "open_url",
        {"url": "https://example.com/", "timeout": 1},
    )
    second = hercules._action_signature(
        "open_url",
        {"url": "https://example.com", "timeout": 10, "force_new_tab": True},
    )

    assert first == second
    assert hercules._action_already_executed(
        [{"action_signature": first, "status": "loaded"}],
        second,
    )


def test_assertion_fails_when_plan_has_pending_steps() -> None:
    hercules = SimpleHercules("smoke")

    result = hercules._assertion_node(
        {
            "task": "Signup page",
            "evidence": [{"tool": "get_page_text", "summary": "Home Signup / Login"}],
            "plan": [
                {"step": "Navigate to home page", "status": "completed"},
                {"step": "Click Signup / Login button", "status": "pending"},
                {"step": "Verify New User Signup message", "status": "pending"},
            ],
            "memory_updates": [],
            "messages": [],
        }
    )

    assert result["is_passed"] is False
    assert result["final_response"] == "The test failed."
    assert "2 pending step(s)" in result["assert_summary"]
