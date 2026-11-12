"""Every tuned hip-hop lane declares the master soft clip it actually ships.

Context (2026-08-12): `GenreProfile.soft_clip` was False on drill, west_coast,
detroit and pluggnb while every other tuned lane had it True. That was reported
as a clipping risk. It is not one — `flp_writer._master_soft_clip_enabled`
returns True whenever `plg_use_template` is not False, and nothing on the
active route sets it False, so the template's Master Soft Clipper shipped on
those lanes anyway (confirmed by reading the exported FLP bytes). The flag was
simply metadata that disagreed with the export, which is worth fixing on its
own: `mix_blueprint` and the template-less export path both read it.
"""
from __future__ import annotations

from genre_profiles import DETAILED_GENRES, PROFILES


def test_every_tuned_lane_declares_soft_clip():
    missing = sorted(name for name in DETAILED_GENRES if not PROFILES[name].soft_clip)
    assert missing == [], f"tuned lanes still declaring no master soft clip: {missing}"


def test_flag_matches_what_the_template_export_actually_does():
    from flp_writer import _master_soft_clip_enabled

    for name in sorted(DETAILED_GENRES):
        pattern = {"style": name, "plg_genre": name}
        assert _master_soft_clip_enabled(pattern) is PROFILES[name].soft_clip, name


def test_template_less_export_still_honours_the_flag():
    """The one path where the flag is not inert — keep it meaningful."""
    from flp_writer import _master_soft_clip_enabled

    off = {"plg_use_template": False, "plg_fl_fx": True,
           "plg_producer_meta": {"master_soft_clip": False}}
    on = {"plg_use_template": False, "plg_fl_fx": True,
          "plg_producer_meta": {"master_soft_clip": True}}
    assert _master_soft_clip_enabled(off) is False
    assert _master_soft_clip_enabled(on) is True
