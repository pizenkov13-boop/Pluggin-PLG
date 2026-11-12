# PLG FL Studio Themes (V3)

Three dark themes for FL Studio that match the PLG vibe. No purple AI gradients —
near-black rooms, chrome, blood red.

| Theme | Vibe | BG | Accent |
|---|---|---|---|
| **Opium Dark** | Carti / opium trap. PLG default. | `#0a0a0a` | blood red `#8b0000` |
| **Chrome Hearts** | Liquid chrome, cold luxury. | `#050505` | chrome `#c0c0c0` |
| **Balenciaga Neon** | Dark minimal, one neon edge. | `#0a0a0a` | neon red `#ff2d2d` |

Files live in [`themes/`](themes): one `*.json` color spec per theme.

---

## Important: the `.flstheme` format

FL Studio themes are `.flstheme` files in
`Documents/Image-Line/FL Studio/Settings/Themes/`. That format is a **proprietary
binary** produced only by FL's built-in **theme editor** — it cannot be authored
from scratch outside FL. (See
[Theme Settings](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/envsettings_themes.htm).)

So PLG ships **exact color specs** (the hex values) plus an installer. You apply
the values once in FL's theme editor and save a real `.flstheme`. After that the
theme is reusable and shareable like any other.

---

## Install

```powershell
python theme_install.py
```

This:
- copies any real `.flstheme` files you've put in `themes/` straight into the FL
  Themes folder, and
- copies the color specs to `…/Settings/Themes/PLG/` so the hex values are next
  to FL.

Then in FL: **Options → General settings → Theme** (or the theme picker) to
choose an installed `.flstheme`.

---

## Apply a color spec in the FL theme editor

1. FL: open the **theme editor** (theme picker → *Edit current theme*).
2. Open the matching `themes/<name>.json` — the `il_theme_editor` block maps the
   key UI regions to hex values:

   ```json
   "il_theme_editor": {
     "Main background": "#0a0a0a",
     "Panel background": "#141414",
     "Knob / control": "#8b0000",
     "Highlight / selection": "#8b0000",
     "Text": "#e6e6e6",
     "Dimmed text": "#7a7a7a"
   }
   ```
3. Set each region to its hex value, then **save the theme** with the PLG name.
   FL writes the `.flstheme`. Drop that file back into `themes/` and re-run
   `theme_install.py` to make it a one-click install for everyone.

---

## Color tokens (full)

Each `themes/<name>.json` `colors` block defines: `background`,
`background_alt`, `panel`, `channel_rack`, `accent`, `accent_hover`,
`selection`, `text`, `text_dim`, `grid_line`, `playhead`. The same tokens can
feed the PLG app theme and any marketing assets, so the DAW and the app match.

---

## Previews

Drop PNG mockups in `themes/previews/` (e.g. `opium_dark.png`) for the TikTok /
gallery. Filenames should match the theme slug. *(Cursor owns the in-app Themes
gallery tab and marketing renders; this repo just ships valid specs + installer.)*
