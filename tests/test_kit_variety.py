from pathlib import Path

from kit_variety import last_kit_paths, pick_with_variety, save_kit_pick


def test_kit_variety_blocks_last_paths(tmp_path: Path):
    a = tmp_path / "kick.wav"
    b = tmp_path / "snare.wav"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    save_kit_pick({"kick": a, "snare": b})
    blocked = last_kit_paths()
    assert str(a.resolve()) in blocked
    assert str(b.resolve()) in blocked


def test_pick_with_variety_targets_described_timbre(tmp_path: Path):
    (tmp_path / "808").mkdir()
    files = ["808/dark_distorted_808.wav", "808/clean_bright_sub.wav"]
    for rel in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    catalog = {"audio_total": 2, "audio": {"808": files}}

    kit = pick_with_variety(catalog, tmp_path, prompt="тёмный жёсткий дисторшн бас", style="opium")
    assert kit["sub_808"][0].name == "dark_distorted_808.wav"


def _hit_wav(path: Path, freq: float, attack_ms: float, ms: int = 150,
             sr: int = 44100, amp: float = 0.8) -> None:
    """A percussive one-shot with a controllable band and attack time."""
    import wave

    import numpy as np

    n = int(sr * ms / 1000)
    t = np.arange(n) / sr
    rise = max(1, int(sr * attack_ms / 1000))
    env = np.concatenate([
        np.linspace(0.0, 1.0, rise),
        np.exp(-np.linspace(0.0, 6.0, max(1, n - rise))),
    ])[:n]
    x = (amp * env * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


def _backbeat_kit(tmp_path: Path, monkeypatch, *, colliding: int):
    """One snare, `colliding` claps sharing its role, plus one that does not.

    Names and folder are identical across candidates, so the ±8 rank jitter
    decides the baseline winner. The assertions below therefore test the
    invariant ("the settled clap never shares the snare's role"), never a
    particular ranking.
    """
    import kit_variety

    monkeypatch.setattr(kit_variety, "HISTORY_FILE", tmp_path / "history.json")
    (tmp_path / "kits").mkdir(exist_ok=True)
    _hit_wav(tmp_path / "kits" / "AAA Snare.wav", 5000, attack_ms=1.0)
    files = ["kits/AAA Snare.wav"]
    for i in range(colliding):
        name = f"kits/{chr(65 + i) * 3} Clap.wav"
        _hit_wav(tmp_path / name, 5040, attack_ms=1.2)
        files.append(name)
    # Its own band and a spread attack — body below, click and width above.
    _hit_wav(tmp_path / "kits" / "ZZZ Clap.wav", 1500, attack_ms=30.0)
    files.append("kits/ZZZ Clap.wav")
    return {"audio_total": len(files), "audio": {"kits": files}}


def _both_arms(catalog, tmp_path: Path, monkeypatch):
    import audio_features

    real = audio_features.roles_collide
    monkeypatch.setattr(audio_features, "roles_collide", lambda a, b: False)
    off = pick_with_variety(catalog, tmp_path, variety_seed=1)
    monkeypatch.setattr(audio_features, "roles_collide", real)
    on = pick_with_variety(catalog, tmp_path, variety_seed=1)
    return off, on


def test_role_split_never_settles_on_a_clap_sharing_the_snares_role(tmp_path: Path, monkeypatch):
    import audio_features

    catalog = _backbeat_kit(tmp_path, monkeypatch, colliding=3)
    _off, on = _both_arms(catalog, tmp_path, monkeypatch)

    # Whichever of the four the scorer ranked first, the lane must end on the
    # one clap that holds a different role.
    assert on["clap"][0].name == "ZZZ Clap.wav"
    assert not audio_features.roles_collide(on["clap"][0], on["snare"][0])


def test_role_split_leaves_the_kit_alone_when_the_roles_already_differ(tmp_path: Path, monkeypatch):
    # No candidate collides, so the veto must be a complete no-op — including
    # for lanes picked AFTER the clap. An earlier version filtered inside the
    # scorer and shifted those even where nothing collided.
    catalog = _backbeat_kit(tmp_path, monkeypatch, colliding=0)
    off, on = _both_arms(catalog, tmp_path, monkeypatch)

    assert {k: v[0].name for k, v in on.items()} == {k: v[0].name for k, v in off.items()}
