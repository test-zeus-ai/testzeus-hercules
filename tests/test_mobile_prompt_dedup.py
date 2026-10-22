"""The mobile agent prompt carries only its own UI-assertion guidance (PR #160 review)."""


def test_mobile_agent_skips_generic_ui_assertion_addendum(monkeypatch) -> None:
    from testzeus_hercules.core.agents import mobile_nav_agent, multimodal_base_nav_agent
    import testzeus_hercules.utils.llm_helper as llm_helper

    sentinel = "\nGENERIC-PAGE-TEXT-ADDENDUM"
    monkeypatch.setattr(multimodal_base_nav_agent, "ui_assertion_prompt_addendum", lambda name: sentinel)
    monkeypatch.setattr(llm_helper, "create_chat_model", lambda *a, **k: object())
    monkeypatch.setattr(multimodal_base_nav_agent.MultimodalBaseNavAgent, "get_ltm", lambda self: None)
    monkeypatch.setattr(multimodal_base_nav_agent.MultimodalBaseNavAgent, "register_tools", lambda self: None)
    monkeypatch.setattr(mobile_nav_agent.MobileNavAgent, "register_tools", lambda self: None)

    mobile = mobile_nav_agent.MobileNavAgent({"model": "m"}, {}, None, agent_prompt="MOBILE PROMPT")
    assert mobile.system_message.startswith("MOBILE PROMPT")
    assert "GENERIC-PAGE-TEXT-ADDENDUM" not in mobile.system_message

    other = multimodal_base_nav_agent.MultimodalBaseNavAgent({"model": "m"}, {}, None, agent_name="x", agent_prompt="OTHER")
    assert "GENERIC-PAGE-TEXT-ADDENDUM" in other.system_message
