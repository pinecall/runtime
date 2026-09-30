"""The prompt's blocks, and the request each turn sends: cached prefix first, the view last."""

import json

import pytest
from livekit.agents import llm

from pinecall.domain.agent import DEFAULT_LAYOUT, Greeting, PromptBlock, block_hash
from pinecall.domain.errors import DeclarationRefused
from pinecall.providers.build import a_list, a_mapping
from pinecall.session._prompt import (
    Blocks,
    Request,
    greeting_for,
    knowledge_changed,
    request,
)

LAYOUT = (
    PromptBlock("identity", "static"),
    PromptBlock("rules", "static"),
    PromptBlock("view", "dynamic"),
    PromptBlock("stage", "dynamic"),
)


def _written(**texts: str) -> Blocks:
    blocks = Blocks(LAYOUT)
    for name, text in texts.items():
        blocks.set(name, text)
    return blocks


def _history() -> llm.ChatContext:
    history = llm.ChatContext.empty()
    history.add_message(role="assistant", content="Hola, clínica.")
    history.add_message(role="user", content="quiero un turno")
    return history


def _with_instructions(blocks: Blocks) -> llm.ChatContext:
    history = _history()
    history.items.insert(0, llm.ChatMessage(role="system", content=[blocks.instructions]))
    return history


# What a format that keeps system messages apart was handed as them.
def _system(params: Request) -> list[str]:
    _, data = params.to_provider_format("anthropic")
    text: object = getattr(data, "system_messages", None)
    return [str(item) for item in text] if a_list(text) else []


def _tool_results(params: Request) -> list[str]:
    messages, _ = params.to_provider_format("anthropic")
    found: list[str] = []
    for message in messages:
        content = message.get("content")
        for part in content if a_list(content) else []:
            if a_mapping(part) and part.get("type") == "tool_result":
                inner = part.get("content")
                found.append(inner if isinstance(inner, str) else json.dumps(inner))
    return found


def _lookup() -> list[llm.ChatItem]:
    found = json.dumps({"facts": [{"text": "alérgico a la penicilina"}]})
    return [
        llm.FunctionCall(call_id="lu_1_recall", name="recall", arguments='{"query": "turno"}'),
        llm.FunctionCallOutput(call_id="lu_1_recall", name="recall", output=found, is_error=False),
    ]


def test_the_default_layout_with_nothing_declared_is_the_four_blocks() -> None:
    assert [block.name for block in Blocks().layout] == ["identity", "knowledge", "tools", "view"]
    assert Blocks().layout == DEFAULT_LAYOUT


def test_the_static_blocks_join_into_one_instructions_string_in_layout_order() -> None:
    blocks = _written(rules="Sé breve.", identity="Sos la recepción.")
    assert blocks.instructions == "Sos la recepción.\n\nSé breve."


def test_a_block_nobody_wrote_is_not_sent_and_leaves_no_blank_between_the_others() -> None:
    blocks = Blocks(
        (PromptBlock("a", "static"), PromptBlock("b", "static"), PromptBlock("c", "static"))
    )
    blocks.set("a", "uno")
    blocks.set("c", "tres")
    assert blocks.instructions == "uno\n\ntres"


def test_writing_a_static_block_says_the_instructions_moved_and_a_dynamic_one_does_not() -> None:
    blocks = Blocks(LAYOUT)
    assert blocks.set("identity", "Sos la recepción.")
    assert not blocks.set("view", "Turnos libres: lunes")


def test_rewriting_a_block_with_its_own_bytes_is_no_update() -> None:
    blocks = _written(identity="Sos la recepción.")
    assert not blocks.set("identity", "Sos la recepción.")


def test_a_name_outside_the_layout_is_refused_with_the_name_and_the_names_that_are_in_it() -> None:
    with pytest.raises(
        DeclarationRefused, match=r"no block named 'menu': .*identity, rules, view, stage"
    ):
        Blocks(LAYOUT).set("menu", "x")


def test_a_declared_layout_keeps_its_own_order_within_each_region() -> None:
    blocks = _written(stage="Paso 2", view="Turnos", rules="Sé breve.", identity="Recepción")
    assert blocks.of("static") == ("Recepción", "Sé breve.")
    assert blocks.of("dynamic") == ("Turnos", "Paso 2")


def test_the_file_a_class_ships_with_is_read_into_the_knowledge_block() -> None:
    assert Blocks(DEFAULT_LAYOUT, "# Precios").instructions == "# Precios"


def test_a_class_that_ships_no_file_has_an_empty_knowledge_block() -> None:
    assert Blocks(DEFAULT_LAYOUT).texts["knowledge"] == ""


def test_a_format_that_keeps_system_messages_apart_gets_one_per_static_block_in_order() -> None:
    blocks = _written(identity="Recepción", rules="Sé breve.")
    history = _history()
    history.items.insert(0, llm.ChatMessage(role="system", content=[blocks.instructions]))
    assert _system(request(history, blocks)) == ["Recepción", "Sé breve."]


def test_rewriting_one_block_leaves_the_blocks_before_it_byte_identical() -> None:
    first = _written(identity="Recepción", rules="Sé breve.")
    second = _written(identity="Recepción", rules="Sé muy breve.")
    before = _system(request(_with_instructions(first), first))
    after = _system(request(_with_instructions(second), second))
    assert before[0] == after[0]
    assert before[1] != after[1]


def test_every_other_format_reads_the_static_blocks_as_the_one_joined_string() -> None:
    blocks = _written(identity="Recepción", rules="Sé breve.")
    history = _history()
    history.items.insert(0, llm.ChatMessage(role="system", content=[blocks.instructions]))
    messages, _ = request(history, blocks).to_provider_format("openai")
    assert messages[0] == {"role": "system", "content": "Recepción\n\nSé breve."}


def test_the_dynamic_blocks_land_after_the_history_in_layout_order_one_message_each() -> None:
    params = request(_history(), _written(stage="Paso 2", view="Turnos libres"))
    tail = [item.text_content for item in params.items[-2:] if isinstance(item, llm.ChatMessage)]
    assert tail == ["Turnos libres", "Paso 2"]


def test_the_history_livekit_handed_in_is_left_exactly_as_it_was() -> None:
    history = _history()
    before = list(history.items)
    request(history, _written(view="Turnos"), _lookup())
    assert history.items == before


def test_a_block_nobody_wrote_sends_nothing_and_the_request_is_the_history_alone() -> None:
    history = _history()
    assert request(history, Blocks(LAYOUT)).items == history.items


def test_a_lookup_lands_ahead_of_the_caller_so_their_words_stay_next_to_the_view() -> None:
    params = request(_history(), _written(view="Turnos"), _lookup()).items
    kinds = [item.type for item in params]
    assert kinds == ["message", "function_call", "function_call_output", "message", "message"]
    assert isinstance(params[3], llm.ChatMessage)
    assert params[3].text_content == "quiero un turno"


def test_a_turn_the_caller_did_not_open_keeps_its_lookups_at_the_end() -> None:
    history = llm.ChatContext.empty()
    history.add_message(role="assistant", content="¿Algo más?")
    params = request(history, Blocks(LAYOUT), _lookup()).items
    assert [item.type for item in params] == ["message", "function_call", "function_call_output"]


def test_both_halves_of_a_pair_survive_the_formatter_in_order() -> None:
    messages, _ = request(_history(), Blocks(LAYOUT), _lookup()).to_provider_format("openai")
    roles = [message["role"] for message in messages]
    assert roles.index("tool") == roles.index("assistant", 1) + 1


def test_a_lookups_content_parses_as_json_and_no_fact_reaches_the_system_field() -> None:
    blocks = _written(identity="Recepción")
    params = request(_with_instructions(blocks), blocks, _lookup())
    assert "penicilina" not in "".join(_system(params))
    (data,) = _tool_results(params)
    facts = json.loads(data)["facts"]
    assert facts[0]["text"] == "alérgico a la penicilina"


def test_the_view_is_never_placed_in_a_tool_result() -> None:
    results = _tool_results(request(_history(), _written(view="Turnos libres"), _lookup()))
    assert results
    assert all("Turnos libres" not in result for result in results)


def test_a_call_a_person_opened_is_greeted_the_way_the_class_declared() -> None:
    greeting = Greeting(say="Buenas")
    assert greeting_for(greeting, None) == greeting


def test_a_call_a_run_opened_is_not_greeted_at_all() -> None:
    assert greeting_for(Greeting(say="Buenas"), "run_1") is None
    assert greeting_for(Greeting(reply="Saluda"), "run_1") is None


def test_a_class_that_declares_no_greeting_is_unchanged_by_any_of_this() -> None:
    assert greeting_for(None, None) is None


def test_a_greeting_that_names_neither_verb_or_both_is_refused_at_declaration() -> None:
    with pytest.raises(DeclarationRefused, match="neither was"):
        Greeting()
    with pytest.raises(DeclarationRefused, match="both were declared"):
        Greeting(say="a", reply="b")


def test_the_file_a_class_ships_with_gets_a_line_of_its_own_and_none_ships_none() -> None:
    changed = knowledge_changed(Blocks(DEFAULT_LAYOUT, "# Precios"))
    assert changed is not None
    assert (changed.name, changed.chars, changed.hash) == ("knowledge", 9, block_hash("# Precios"))
    assert knowledge_changed(Blocks(DEFAULT_LAYOUT)) is None


def test_a_config_update_stays_in_the_history_and_never_reaches_the_provider() -> None:
    history = _history()
    history.items.append(llm.AgentConfigUpdate(instructions="Sos otra recepción"))
    params = request(history, Blocks(LAYOUT))
    assert any(isinstance(item, llm.AgentConfigUpdate) for item in params.items)
    messages, _ = params.to_provider_format("openai")
    assert "Sos otra recepción" not in json.dumps(messages)


def test_the_knowledge_the_platform_wrote_reaches_the_model_as_its_own_system_block() -> None:
    blocks = Blocks(DEFAULT_LAYOUT, "# Precios: consulta 30 EUR")
    blocks.set("identity", "Sos la recepción.")
    assert _system(request(_with_instructions(blocks), blocks)) == [
        "Sos la recepción.",
        "# Precios: consulta 30 EUR",
    ]
