# Archived: React components that were never mounted

Archived 2026-08-11. Verified against the built bundle
(`web/dist/assets/index-*.js`) — none of these strings appear in it, so nothing
users run today loses a feature.

| File | Was | Category |
|---|---|---|
| `BeatComposer.tsx` / `.css` | the old prompt-driven composer card | superseded by `HomeHero` + `HomeView` |
| `SlashMenu.tsx` / `.css` | `/`-command palette for the prompt box | superseded — there is no prompt box |
| `AppHeader.tsx` / `.css` | top bar with the language switcher | superseded by `Sidebar` |
| `ProducerConsole.tsx` / `.css` | chaos roll / flip beat / clipper toggle | Re-bake family, retired by product contract |
| `BlueprintChecklist.tsx` / `.css` | producer-blueprint step list | depended on the same retired route |
| `featureFlags.ts` | flag helper | nothing imported it |

## Backend surface removed with them

`plg_webview.Api` no longer exposes `chaos_roll`, `flip_beat`,
`set_clipper_mode`, `set_filth_mode`, `get_producer_blueprint`. The `plg_api`
functions are kept private as `_archived_*`. `pattern_tools.py` itself stays —
the export path still uses `load_pattern` / `save_pattern` /
`finalize_pattern_exports`.

## Note on `LangSwitcher`

`AppHeader` imported `LangSwitcher`, but `LangSwitcher` is still live: `AuthView`
and `Sidebar` both use it. It was not archived.
