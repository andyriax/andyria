from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from andyria.memory import ContentAddressedMemory
from andyria.models import PromptFlowInputRequest, PromptFlowStartRequest
from andyria.projections import PromptFlowStore


def _make_store(tmp_path):
    memory = ContentAddressedMemory(
        data_dir=tmp_path,
        node_id="test-node",
        private_key=Ed25519PrivateKey.generate(),
    )
    return PromptFlowStore(memory)


def test_game_builder_collects_richer_inputs(tmp_path):
    store = _make_store(tmp_path)

    response = store.start(PromptFlowStartRequest(kind="game_builder"))

    assert response.kind == "game_builder"
    assert response.total_steps == 10
    assert response.prompt == "What type of game do you want to create?"

    answers = [
        "2",
        "3",
        "1",
        "3",
        "2",
        "Fight through short dungeon runs, extract loot, and upgrade between runs.",
        "5",
        "2",
        "1",
        "2",
    ]

    for answer in answers:
        response = store.respond(response.flow_id, PromptFlowInputRequest(input=answer))

    assert response.completed is True
    assert response.answers["type"] == "Roguelike"
    assert response.answers["platform"] == "Web"
    assert response.answers["camera"] == "Isometric"
    assert response.answers["audience"] == "Casual players"
    assert response.answers["progression"] == "Meta progression"
    assert response.answers["monetization"] == "Free prototype / jam build"
    assert "Camera: Isometric" in (response.summary or "")
    assert "Monetization: Free prototype / jam build" in (response.summary or "")
    assert "- Camera/presentation: Isometric" in (response.backend_prompt or "")
    assert "- Target audience: Casual players" in (response.backend_prompt or "")
    assert "- Progression model: Meta progression" in (response.backend_prompt or "")
    assert "11) Playtest instrumentation and success metrics" in (response.backend_prompt or "")


def test_game_builder_rejects_invalid_choice_without_advancing(tmp_path):
    store = _make_store(tmp_path)

    response = store.start(PromptFlowStartRequest(kind="game_builder"))
    invalid = store.respond(response.flow_id, PromptFlowInputRequest(input="unknown option"))

    assert invalid.completed is False
    assert invalid.step == 1
    assert invalid.message == "Please choose one of the available options."
