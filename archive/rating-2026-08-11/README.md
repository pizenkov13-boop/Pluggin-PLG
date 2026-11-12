# Archived: beat rating (👍/👎) and the learned reward loop

Archived 2026-08-11 by product decision.

## Why

PLG's goal is to be right on the **first** generation. It is not a system that
improves over weeks by accumulating user ratings — that is a different product
philosophy and it is explicitly not this one.

## What is here

| File | Was |
|---|---|
| `BeatDock.tsx` / `BeatDock.css` | the 👍/👎 dock, never mounted in the shipped UI |
| `test_beat_rating_api.py` | tests for `plg_api.record_beat_rating` |

## What changed in the live tree

- `plg_webview.Api.record_beat_rating` / `.beat_reward_status` — removed from
  the JS bridge.
- `plg_api.record_beat_rating` → `_archived_record_beat_rating`,
  `beat_reward_status` → `_archived_beat_reward_status` (kept private, matching
  the existing `_archived_regenerate` / `_archived_rebake_session` convention).
- `web/src/api.ts` — `recordBeatRating`, `beatRewardStatus` and their
  `PywebviewApi` declarations removed.
- `web/src/devBridge.ts` — matching dev stubs removed.

## What deliberately stayed

`beat_reward.py` is **not** archived. Three live modules import it defensively:

- `beat_quality` → `predict_reward_score` / `reward_enabled` / `reward_weight`
- `generation_variety` → `load_ratings` / `taste_profile`
- `style_corpus` → `fleet_reward_enabled` / `global_progression_stats`

It is already inert: `reward_enabled()` returns `False` because scikit-learn is
not installed, and there are no ratings on disk, so nothing it exposes changes a
generated beat. Ripping it out would mean surgery on the quality scorer and the
variety engine for zero behavioural gain. It stays as an inert dependency; the
user-facing rating loop is what is gone.

`plg_api.get_status()` still reports `reward_*` fields. They are harmless
metadata and no mounted component reads them.
