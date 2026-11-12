# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — PLG.exe with starter sounds, bundled kit, and web UI."""

import os
import sys
from pathlib import Path

block_cipher = None
root = Path(SPECPATH)

# Tester flavor (build_plg.bat tester): PLG_BUILD_FLAVOR_FILE names a build_flavor.json that is added to the bundle
# of THAT exe alone (app_config.build_flavor reads it; nothing else can switch the tester mode on). It also swaps
# .env.release (server address, public key) for the cloud-free .env.tester and names the exe PLG_Tester. A release
# build never sets the variable (the bat clears it) and tools/verify_build_flavor.py checks the finished exe.
_flavor_file = os.environ.get("PLG_BUILD_FLAVOR_FILE", "").strip()
TESTER_BUILD = bool(_flavor_file)
if TESTER_BUILD and not Path(_flavor_file).is_file():
    raise SystemExit(f"tester build: {_flavor_file} does not exist (run build_plg.bat tester)")
EXE_NAME = "PLG_Tester" if TESTER_BUILD else "PLG"

# DD (2026-08-27): every WAV under assets/starter/ is gitignored, so a clean
# checkout has none of them. Regenerate them from the first-party synthesis
# code and verify them against assets/starter/GENERATED.json *before* datas is
# built. This used to be guarded per-file with `if ....is_file()`, which meant
# a clean-room build silently produced an exe with missing audio.
sys.path.insert(0, str(root / "tools"))
import build_starter_assets

build_starter_assets.ensure_ready()

starter_names = (
    "PLG_starter_808.wav",
    "PLG_starter_808_bass.wav",
    "PLG_starter_hat.wav",
    "PLG_starter_melody.wav",
    "PLG_starter_kick.wav",
    "PLG_starter_snare.wav",
    "PLG_starter_clap.wav",
    "BUNDLED.json",
    "starter_manifest.json",
    # README.md (how to regenerate) and GENERATED.json (the build-time hash
    # contract that ensure_ready() above checks against the repo copy) are
    # developer files: nothing reads them at runtime, so they are not shipped.
)

# No `is_file()` guard: ensure_ready() has already proven the audio exists and
# hashes correctly, and the tracked text files are in git. A missing entry here
# is a real packaging fault and must stop the build, not shrink the payload.
starter_files = []
for name in starter_names:
    source = root / "assets" / "starter" / name
    if not source.is_file():
        raise SystemExit("PLG.spec: required starter asset is missing: %s" % source)
    starter_files.append((str(source), "assets/starter"))

bundled_dir = root / "assets" / "starter" / "bundled_sounds"
if not bundled_dir.is_dir():
    raise SystemExit("PLG.spec: bundled_sounds/ is missing after generation: %s" % bundled_dir)
for path in sorted(bundled_dir.iterdir()):
    if path.is_file():
        starter_files.append((str(path), "assets/starter/bundled_sounds"))

# NOTE (2026-08-27 DD): assets/starter/PLG_Samples/ is deliberately NOT
# bundled. Its WAVs come from commercial sample packs whose licenses do not
# grant redistribution inside software (the project issue log (not included) §0af). The folder may
# still exist in a dev checkout — melody_sample_bank.pick_melody_sample reads
# it there — but a shipping build carries none of it: an empty bank returns
# (None, 0.0) and the melody channel exports without a forced sample.
# Any number of files: drop new WAVs into bundled_sounds/ and rebuild the
# .exe — the loop below picks them all up; no hardcoded count and no code
# change needed.

# NOTE (2026-08-27 DD): assets/flex/*.bin are deliberately NOT bundled.
# They are captured FLEX plugin-state bytes of unreproducible provenance
# (FLP_PROVENANCE.md, the project issue log (not included) §0ag). Without them the export path
# degrades on its own: _flex_blob_paths() -> None, melody channel exports
# through the existing Sampler path (starter/user melody wav); the stamped
# FLEX picks are ignored at build time. The files stay in dev checkouts.

asset_files = [
    (str(root / "assets" / name), "assets")
    for name in (
        "logo.svg",
        "logo.png",
        "logo.ico",
        "logo_icon.png",
        "Frame 2.svg",
        "prompt_tags.json",
        "mix_blueprint_i18n.json",
        "genre_profiles.json",
        "hat_patterns.json",  # hat_patterns.library(): without it the hat lane only has its own five base lines
        "plg_mixer_reference_params.bin",
    )
    if (root / "assets" / name).is_file()
]

# PLG's own melody one-shots (Serum 2 factory presets): <type>/*.wav only. _rejected/ and sources.txt stay out; the
# list comes from melody_oneshots.library, the same rule the app uses to find them (a missing folder stops the build).
import melody_oneshots_bundle

melody_oneshot_files = melody_oneshots_bundle.bundle_files(root)

icon_path = root / "assets" / "logo.ico"
exe_icon = str(icon_path) if icon_path.is_file() else None

font_files = [
    (str(path), "assets/fonts")
    for path in (root / "assets" / "fonts").glob("*.ttf")
]

web_dist = root / "web" / "dist"
# PyInstaller's datas dest is the target *directory*, not the file path — so use
# the file's parent dir. (Using the full path nested index.html under a folder
# literally named "index.html", which broke the built UI at runtime.)
web_files = [
    (str(path), str(Path("web/dist") / path.relative_to(web_dist).parent))
    for path in web_dist.rglob("*")
    if path.is_file()
] if web_dist.is_dir() else []

# Only what the running app reads from the bundle. Developer documents
# (internal notes (not included) / FL_*.md), .env.example and user_profile.example.json are NOT
# shipped: the Help screen opens only the legal docs below, and in the frozen
# app .env.example is looked up next to the exe (PROJECT_DIR), never in the bundle.
root_data_files = [
    "plugin_script.py",  # fl_setup installs it into FL Studio
    "THIRD_PARTY_NOTICES.md",  # license compliance
    ".env.tester" if TESTER_BUILD else ".env.release",  # app_config seeds the exe's .env from it
]

# User-facing legal docs shown in Help (plg_api.DOC_FILES / DOCS_DIR),
# named explicitly. This snapshot ships placeholder texts only.
legal_data_files = [
    "TERMS_RU.md",
    "PRIVACY_RU.md",
    "PAYMENT_TERMS_RU.md",
]

datas = starter_files + asset_files + melody_oneshot_files + font_files + web_files + [
    (str(root / name), ".")
    for name in root_data_files
    if (root / name).is_file()
] + ([(_flavor_file, ".")] if TESTER_BUILD else []) + [
    (str(root / "legal" / name), "legal")
    for name in legal_data_files
    if (root / "legal" / name).is_file()
] + [
    (str(root / "fl_scripts"), "fl_scripts"),
    # themes/ is not shipped: only the legacy tkinter plg_app / theme_install
    # read it; the webview UI does not.
    # DD license compliance (2026-08-27): canonical third-party license texts
    # + notices must accompany the shipped dependencies (OFL fonts, MPL CA
    # bundle, permissive Python/JS deps). See THIRD_PARTY_NOTICES.md.
    (str(root / "third_party_licenses"), "third_party_licenses"),
]

hiddenimports = [
    "pretty_midi",
    "param_plan",
    "param_plan_client",
    "mido",
    "dotenv",
    "webview",
    "httpx",
    "sentry_sdk",
    "beat_humanize",
    "hat_roll_engine",
    "sample_chop_engine",
    "kit_variety",
    "mix_blueprint",
    "producer_brain",
    "drum_defaults",
    "drum_blueprint",
    # sub_808_gen stays a hidden import only because midi_validate imports its
    # register/length constants to *reject* sub_808 material. The lane itself is
    # permanently disabled (midi_validate.MIDI_DISABLED_LANES).
    "sub_808_gen",
    "hihat_rolls",
    "mix_gain_staging",
    "flp_writer",
    "sample_match",
    "build_bundled_sounds",
    "arranger",
    "audio_features",
    # imported lazily (stamped after humanize / during scan / at kit pick) and
    # all fail-open, so a missing one would silently turn the feature off
    "style_card",
    "sample_passport",
    "kit_coherence",
    "melody_oneshots",  # imported lazily by starter_kit and fail-open: a missing module would silently drop the sound
    # ffmpeg-free WAV (numpy) / FLAC (soundfile) decoding; imported lazily.
    "audio_decode",
    "wav_decode",
    "soundfile",
    "midi_ingest",
    "music_theory",
    "genre_profiles",
    "sound_descriptors",
    "plg_webview",
    "plg_api",
    "plg_cloud",
    "plg_net",
    "plg_device",
    "plg_sentry",
    "plg_log",
    "plg_analytics",
]

a = Analysis(
    ["PLG.pyw"],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Stem splitting was archived 2026-08-11, so nothing here needs torch.
    # These stay excluded so an incidental transitive import can never drag the
    # ~200 MB torch stack into the shipped exe.
    excludes=[
        "torch",
        "torchaudio",
        "torchvision",
        "demucs",
        "tensorflow",
        # 2026-10: scikit-learn (+ its scipy) and tiktoken reached the dev Python as dependencies of
        # librosa / openai-whisper and added ~38 MB to the exe. The app uses none of them:
        # beat_reward imports sklearn lazily behind try/except ImportError (the rating vertical is
        # archived), and nothing else imports scipy or tiktoken. The September exes shipped without them.
        "sklearn",
        "scipy",
        "tiktoken",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=exe_icon,
)
