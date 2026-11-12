import { useEffect, useMemo, useRef, useState } from "react";
import { checkFlExecutable, getSettings, pickFlExecutable, ready, saveSettings } from "../api";
import type { FlInstallation } from "../types";
import { useI18n } from "../i18n";
import { apiErrorMessage } from "../errors";
import ConfirmModal from "./ConfirmModal";
import type { ArchiveTarget } from "../api";
import { IconFolder, IconWarning } from "./icons";
import "./SettingsView.css";
import "./PageView.css";

type Props = {
  onSaved: () => void;
  // Set when App.tsx redirects here after "Open in FL" failed with
  // fl_not_found — this view owns its own error state and never otherwise
  // sees that failure, so without this the user lands here with no
  // explanation of why. Seeds the initial error on this one mount only.
  initialError?: string | null;
  // Moves the chosen folders' files into PLG_Archive; never deletes. The caller
  // runs it and refreshes the counters.
  onArchive: (targets: ArchiveTarget[]) => Promise<void>;
  archiveDisabled?: boolean;
  midiCount: number;
  audioCount: number;
};

type ArchiveChoice = "midi" | "library" | "both";

// Cheap first filter only — matching this does NOT mean the file exists, it
// just rules out obviously-wrong input before bothering the backend. The
// green checkmark itself is decided by the real on-disk check below.
function looksLikeFlExe(path: string): boolean {
  return /[/\\](FL64|FL)\.exe$/i.test(path.trim());
}

export default function SettingsView({
  onSaved,
  initialError,
  onArchive,
  archiveDisabled,
  midiCount,
  audioCount,
}: Props) {
  const { t } = useI18n();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveChoice, setArchiveChoice] = useState<ArchiveChoice | null>(null);
  const [archiving, setArchiving] = useState(false);
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [flExecutable, setFlExecutable] = useState("");
  const [installations, setInstallations] = useState<FlInstallation[]>([]);
  const [exists, setExists] = useState(false);
  const checkIdRef = useRef(0);
  const firstCheckRef = useRef(true);

  useEffect(() => {
    ready()
      .then(() => getSettings())
      .then((s) => {
        const installs = s.fl_installations ?? [];
        setInstallations(installs);
        // Prefer saved override; otherwise show the auto-detected newest path.
        const saved = (s.fl_executable || "").trim();
        setFlExecutable(saved || installs[0]?.path || "");
        // Always keep auto-open on — no UI toggle.
        void saveSettings({ auto_open_fl: true });
      })
      .finally(() => setLoading(false));
  }, []);

  // Real existence check, debounced against typing. A known auto-detected
  // install is already backend-vetted (came from discover_fl_installations()),
  // so it's trusted instantly with no round trip. Anything else that at least
  // looks like FL64.exe/FL.exe gets a real filesystem check before the
  // checkmark turns green — the old version just trusted the filename.
  useEffect(() => {
    const path = flExecutable.trim();
    if (!path) {
      setExists(false);
      firstCheckRef.current = false;
      return;
    }
    const knownInstall = installations.some(
      (item) => item.path.toLowerCase() === path.toLowerCase(),
    );
    if (knownInstall) {
      setExists(true);
      firstCheckRef.current = false;
      return;
    }
    if (!looksLikeFlExe(path)) {
      setExists(false);
      firstCheckRef.current = false;
      return;
    }
    const requestId = ++checkIdRef.current;
    const run = () => {
      checkFlExecutable(path).then((result) => {
        if (checkIdRef.current !== requestId) return; // superseded by a newer check
        setExists(Boolean(result.ok && result.exists));
      });
    };
    if (firstCheckRef.current) {
      // The path just loaded from disk settings, not typed — check it right
      // away instead of making the initial render wait out a debounce.
      firstCheckRef.current = false;
      run();
      return;
    }
    const timer = window.setTimeout(run, 300);
    return () => window.clearTimeout(timer);
  }, [flExecutable, installations]);

  const status = useMemo(() => {
    const path = flExecutable.trim();
    if (!path) {
      return { ok: false as const, label: t("settings.flStatusMissing") };
    }
    return exists
      ? { ok: true as const, label: t("settings.flStatusFound") }
      : { ok: false as const, label: t("settings.flStatusMissing") };
  }, [flExecutable, exists, t]);

  async function persist(nextPath: string) {
    setSaving(true);
    setError(null);
    try {
      const result = await saveSettings({
        auto_open_fl: true,
        fl_executable: nextPath.trim(),
      });
      if (!result.ok) {
        // apiErrorMessage()'s fl_not_found copy talks about opening the
        // project file manually — that's the right message for a failed
        // "open in FL" job, not for typing a bad path here, so this one
        // gets its own.
        setError(
          result.error_type === "fl_not_found" ? t("settings.flNotFound") : apiErrorMessage(result, t),
        );
        return false;
      }
      onSaved();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.error"));
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function onSave() {
    await persist(flExecutable);
  }

  function closeArchiveDialog() {
    setArchiveOpen(false);
    setArchiveChoice(null);
  }

  async function onConfirmArchive() {
    if (!archiveChoice) return;
    setArchiving(true);
    setError(null);
    try {
      await onArchive(archiveChoice === "both" ? ["midi", "library"] : [archiveChoice]);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.error"));
    } finally {
      setArchiving(false);
      closeArchiveDialog();
    }
  }

  async function onBrowse() {
    setError(null);
    const result = await pickFlExecutable();
    if (!result.ok) {
      if (!result.cancelled) {
        setError(result.error || t("settings.flPickFailed"));
      }
      return;
    }
    if (!result.path) return;
    setFlExecutable(result.path);
    // Only auto-save a path that actually looks like FL64.exe/FL.exe — the
    // backend re-validates and rejects anything else, which used to surface
    // as a raw "FL executable not found" error the same instant the picker
    // closed, even though the red status line right above already says so.
    if (looksLikeFlExe(result.path)) {
      await persist(result.path);
    }
  }

  if (loading) {
    return <div className="page page--loading">{t("common.loading")}</div>;
  }

  return (
    <div className="page page--figma page--figma-settings">
      <div className="page__head">
        <h1>{t("settings.title")}</h1>
        <p>{t("settings.desc")}</p>
      </div>

      <div className="page__card--figma page__card--figma-settings">
        <div className="settings__path">
          <input
            className="settings__path-input"
            type="text"
            value={flExecutable}
            readOnly
            onClick={onBrowse}
            placeholder={t("settings.flBrowsePlaceholder")}
            spellCheck={false}
            aria-label={t("settings.desc")}
          />
          <button
            type="button"
            className="settings__path-browse"
            onClick={onBrowse}
            aria-label={t("settings.flBrowse")}
            title={t("settings.flBrowse")}
          >
            <IconFolder />
          </button>
        </div>

        <p
          className={`settings__status ${status.ok ? "settings__status--ok" : "settings__status--bad"}`}
          role="status"
        >
          {status.ok ? "✓" : "✕"} {status.label}
        </p>

        <div className="page__row page__row--figma-settings">
          <button
            type="button"
            className="page__hero-btn page__hero-btn--settings"
            onClick={onSave}
            disabled={saving || (flExecutable.trim() !== "" && !status.ok)}
          >
            {saving ? t("common.saving") : t("common.save")}
          </button>
          <button
            type="button"
            className="page__btn page__btn--ghost"
            onClick={() => setArchiveOpen(true)}
            disabled={saving || archiving || archiveDisabled}
          >
            {t("settings.archive")}
          </button>
        </div>
      </div>

      <ConfirmModal
        open={archiveOpen}
        title={t("settings.archiveTitle")}
        message={t("settings.archiveMessage")}
        confirmLabel={t("settings.archiveConfirm")}
        cancelLabel={t("common.cancel")}
        busy={archiving}
        confirmDisabled={archiveChoice === null}
        onConfirm={() => void onConfirmArchive()}
        onCancel={closeArchiveDialog}
      >
        <div className="confirm-modal__choices" role="radiogroup" aria-label={t("settings.archiveTitle")}>
          {(
            [
              ["midi", t("settings.archiveMidi", { count: midiCount })],
              ["library", t("settings.archiveLibrary", { count: audioCount })],
              ["both", t("settings.archiveBoth")],
            ] as const
          ).map(([choice, label]) => (
            <label key={choice} className="confirm-modal__choice">
              <input
                type="radio"
                name="archive-target"
                value={choice}
                checked={archiveChoice === choice}
                onChange={() => setArchiveChoice(choice)}
                disabled={archiving}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
      </ConfirmModal>

      {error && (
        <p className="page__error">
          <IconWarning />
          <span>{error}</span>
        </p>
      )}
    </div>
  );
}
