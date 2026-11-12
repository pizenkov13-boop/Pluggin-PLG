from midi_pocket_mode import choose_midi_pocket_mode


def _passport(role: str, *, tempo_reliable: bool = True):
    return {
        "status": "ok",
        "safety": {"tempo_reliable": tempo_reliable, "key_reliable": True},
        "structure": {"confidence": .9},
        "role": {"musical_role": role},
    }


def test_safe_passport_routes_each_supported_musical_role():
    assert choose_midi_pocket_mode(_passport("sustained_melody"))["mode"] == "sustained_space"
    assert choose_midi_pocket_mode(_passport("chordal"))["mode"] == "chordal_roots"
    assert choose_midi_pocket_mode(_passport("melodic_phrase"))["mode"] == "phrase_answer"


def test_unreliable_passport_preserves_general_pocket():
    mode = choose_midi_pocket_mode(_passport("chordal", tempo_reliable=False))
    assert mode == {"enabled": False, "mode": "general", "reason": "tempo_not_reliable"}
