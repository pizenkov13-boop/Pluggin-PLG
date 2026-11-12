import { useEffect, useRef, useState } from "react";
import {
  archiveMaterial,
  clearPinnedKit,
  getStatus,
  pollJob,
  pingCloudConnection,
  ready,
  cloudBillingStatus,
  beginMaterialImport,
  cancelMaterialImport,
  finishMaterialImport,
  importKitFolder,
  importMaterialChunk,
  installFlScripts,
  pickFolder,
  randomKit,
  startBeat,
  startOpenInFl,
} from "./api";
import type { ArchiveTarget } from "./api";
import type { BeatResult, JobSnapshot, Status } from "./types";
import type { View } from "./types/ui";
import { useI18n } from "./i18n";
import { setUiLocale } from "./api";
import AuthView from "./components/AuthView";
import ErrorBoundary from "./components/ErrorBoundary";
import HomeView from "./components/HomeView";
import SoundBrowser from "./components/SoundBrowser";
import type { DroppedMaterial } from "./components/HomeHero";
import HelpView from "./components/HelpView";
import AccountView from "./components/AccountView";
import SettingsView from "./components/SettingsView";
import Sidebar from "./components/Sidebar";
import OfflineBanner from "./components/OfflineBanner";
import Toaster, { toast } from "./components/Toast";
import { apiErrorMessage, beatErrorMessage, jobErrorMessage, resolveErrorType } from "./errors";
import "./App.css";

const MATERIAL_CHUNK_BYTES = 1024 * 1024;

function base64FromBytes(bytes: Uint8Array): string {
  let binary = "";
  const step = 0x8000;
  for (let start = 0; start < bytes.length; start += step) {
    binary += String.fromCharCode(...bytes.subarray(start, start + step));
  }
  return btoa(binary);
}

export default function App() {
  const { t, locale } = useI18n();
  const [view, setView] = useState<View>(() =>
    import.meta.env.DEV && new URLSearchParams(window.location.search).has("account-preview")
      ? "account"
      : "home",
  );
  const [status, setStatus] = useState<Status | null>(null);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  // refreshStatus runs from timers and effects with stale closures, so it reads
  // busy through a ref. While a job runs, statusLine belongs to the job poll:
  // the generating screen parses its big seconds counter out of that string.
  const busyRef = useRef(false);
  const [statusLine, setStatusLine] = useState(t("status.loading"));
  const [error, setError] = useState<string | null>(null);
  // Settings never receives `error` — it owns its own local error state.
  // Redirecting there after a failed "Open in FL" would otherwise land the
  // user on a plain Settings screen with zero explanation of why they were
  // sent there. Seeds SettingsView's initial error on that one mount only;
  // any manual sidebar navigation clears it so it can't resurface later.
  const [flRedirectError, setFlRedirectError] = useState<string | null>(null);
  const [retryingCloud, setRetryingCloud] = useState(false);
  const [importing, setImporting] = useState(false);
  const [randomizing, setRandomizing] = useState(false);
  const [soundsOpen, setSoundsOpen] = useState(false);
  // The scripts belong to the FL bridge, not to a separate user workflow.
  // Keep one attempt per detected executable for this app launch, so a broken
  // FL/Documents installation cannot create an install loop during polling.
  const flScriptInstallAttemptedFor = useRef<string | null>(null);

  useEffect(() => {
    ready().then(() => {
      setUiLocale(locale);
      refreshStatus();
      // The boot library rescan finishes a moment after the first status: ask once more, so a
      // catalog that was empty at start (local mode never polls) shows its files without a restart.
      window.setTimeout(() => void refreshStatus(), 4000);
    });
    // Cloud mode asks every 30-120 s (the server answer changes). Local and tester builds have no server, but the
    // folders do change (files put in with Explorer, a scan that finishes late), so they ask every 15 s while idle.
    const intervalMs = status?.cloud_mode ? (status.network_online === false ? 30000 : 120000) : 15000;
    const timer = window.setInterval(() => {
      if (status?.cloud_mode || !busyRef.current) refreshStatus();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [status?.cloud_mode, status?.network_online, locale]);

  // Coming back to the window (from Explorer, from FL Studio) refreshes the counters at once.
  useEffect(() => {
    const onFocus = () => {
      if (!busyRef.current) void refreshStatus();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [locale]);

  // The boot rescan of the library is still running: its counters are not final, so ask again soon.
  useEffect(() => {
    if (!status?.library_syncing) return;
    const timer = window.setTimeout(() => void refreshStatus(), 2000);
    return () => window.clearTimeout(timer);
  }, [status]);

  useEffect(() => {
    ready().then(() => setUiLocale(locale));
  }, [locale]);

  // The server probe runs in the background and the status only waits ~1.5 s for it: while it is
  // still running, ask again soon so the offline banner / the Create button settle without a restart.
  useEffect(() => {
    if (!status?.network_checking) return;
    const timer = window.setTimeout(() => void refreshStatus(), 3000);
    return () => window.clearTimeout(timer);
  }, [status]);

  // A network-class error left on screen from a failed job (see
  // finishBeatJob) has no dismiss button and no other auto-clear — once the
  // connection is confirmed back (network_online true again), drop it
  // rather than leaving a stale "no connection" message up. Matched by exact
  // translated text rather than a stored error-type flag, so this can never
  // clear an unrelated error (unsupported file, FL not found, validation…)
  // that just happens to be on screen when connectivity comes back.
  useEffect(() => {
    if (status?.network_online !== false) {
      setError((current) =>
        current === t("errors.network") || current === t("offline.generationFailed") ? null : current,
      );
    }
  }, [status?.network_online, t]);

  useEffect(() => {
    const onNativeMaterialImport = (event: Event) => {
      const result = (event as CustomEvent<{ ok?: boolean; error?: string }>).detail;
      if (result?.ok) {
        setError(null);
        void refreshStatus();
      } else if (result?.error) {
        setError(result.error);
      }
    };
    window.addEventListener("plg-native-material-imported", onNativeMaterialImport);
    return () => window.removeEventListener("plg-native-material-imported", onNativeMaterialImport);
  }, []);

  async function refreshStatus() {
    let s = await getStatus();
    const flExecutable = s.fl_executable ?? null;
    if (
      s.fl_installed &&
      !s.fl_bridge_ready &&
      flExecutable &&
      flScriptInstallAttemptedFor.current !== flExecutable
    ) {
      flScriptInstallAttemptedFor.current = flExecutable;
      const installed = await installFlScripts();
      if (installed.ok) s = await getStatus();
      else setError(apiErrorMessage(installed, t));
    }
    setStatus(s);
    setPrompt((prev) => (prev.trim() ? prev : s.last_prompt?.trim() ?? ""));
    if (busyRef.current) return;
    if (s.cloud_mode && !s.signed_in) {
      setStatusLine(t("auth.title"));
      return;
    }
    if (s.cloud_mode && s.network_online === false) {
      setStatusLine(t("offline.title"));
      return;
    }
    if (!s.cloud_mode && !s.has_api_key) setStatusLine(t("status.needApiKey"));
    else if (s.beat_ready) setStatusLine(t("status.readyOpenFl"));
    else setStatusLine(t("status.readyDescribe"));
  }

  function beginJob() {
    busyRef.current = true;
    setBusy(true);
  }

  function endJob() {
    busyRef.current = false;
    setBusy(false);
  }

  function phaseLabel(phase: string): string {
    const key = `status.phases.${phase}`;
    const label = t(key);
    return label !== key ? label : phase;
  }

  function onJobUpdate(snap: JobSnapshot) {
    if (snap.status === "running") {
      const secs = Math.floor(snap.elapsed);
      const tail =
        secs >= 200
          ? !status?.cloud_mode
            ? t("status.checkNetwork")
            : t("status.longWait")
          : secs >= 20
            ? t("status.longWait")
            : "";
      setStatusLine(t("status.generating", { secs, phase: phaseLabel(snap.phase) }) + tail);
    }
  }

  async function finishBeatJob(final: JobSnapshot) {
    if (final.status !== "done" && final.status !== "error") {
      // "unknown": the backend no longer has this job (restarted, cleared). It
      // is not a success — never report a beat that was not confirmed.
      setError(t("errors.jobLost"));
      setStatusLine(t("status.error"));
      return;
    }
    if (final.status === "error") {
      const errorType = resolveErrorType(final);
      if (errorType === "quota" || errorType === "billing" || errorType === "subscription") {
        // The local quota/billing snapshot was evidently stale (the server just
        // refused the job), so re-read it before the Account screen mounts.
        await refreshStatus();
        openPaywall();
        setStatusLine(t("status.readyDescribe"));
        return;
      }
      setError(beatErrorMessage(final, t));
      setStatusLine(t("status.error"));
      // A network-class failure means our last-known network_online may
      // already be stale — recompute now instead of waiting for the next
      // scheduled poll (30-120s), so the offline banner and this error
      // don't sit out of sync with each other for that long.
      void refreshStatus();
      return;
    }
    const result = (final.result ?? { ok: true }) as unknown as BeatResult;
    if (!result.ok || !result.flp) {
      // A "done" job with no project file is not a beat: say so instead of returning to an empty Home.
      setError(t("errors.genNoFlp"));
      setStatusLine(t("status.error"));
      return;
    }
    setStatusLine(t("status.baked"));
    // The beat is fine, but a built-in sound stands in for a file of the
    // user's own (unreadable, or failed a quality gate) — say so, otherwise
    // they assume PLG just sounds like that.
    const replaced = (result.sample_fallback_lanes ?? [])
      .map((lane) =>
        lane === "808_bass" ? t("notice.lane808") : lane === "hi_hats" ? t("notice.laneHats") : lane === "kick" ? t("notice.laneKick") : "",
      )
      .filter(Boolean);
    if (replaced.length) toast(t("notice.sampleFallback", { lanes: replaced.join(", ") }), 12000);
    const pinnedReplaced = (result.pinned_replaced_lanes ?? []).map((lane) => t(`kit.roles.${lane}`));
    if (pinnedReplaced.length) toast(t("kit.pinnedReplaced", { lanes: pinnedReplaced.join(", ") }), 12000);
    await refreshStatus();
    // Honour the auto_open_fl setting the backend just reported instead of
    // opening FL unconditionally. It defaults to true (app_config.
    // get_auto_open_fl / PLG_AUTO_OPEN_FL), so normal behaviour is unchanged:
    // Create beat still hands the session straight to FL Studio.
    // The tester build says nothing when FL Studio is missing: nothing opens, the beat is in Projects.
    if (result.ok && result.auto_open_fl !== false) await onOpenInFl(Boolean(status?.tester_build));
  }

  async function retryCloudConnection() {
    setRetryingCloud(true);
    try {
      await pingCloudConnection();
      await refreshStatus();
    } finally {
      setRetryingCloud(false);
    }
  }

  async function onCreate() {
    // The drag-and-drop import owns material intake. `prompt` stays
    // compatibility plumbing for plg_api.create_beat and is not musical input.
    const text = prompt.trim() || "custom drumkit beat from detected MIDI and samples";
    const materialReady = (status?.library_audio_total ?? 0) > 0 && (status?.library_midi_total ?? 0) > 0;
    if (!materialReady || busy) return;
    const quotaExhausted = Boolean(
      status?.cloud_mode &&
      status.signed_in &&
      status.quota &&
      !status.quota.skipped &&
      status.quota.remaining <= 0
    );
    if (quotaExhausted) {
      openPaywall();
      return;
    }
    // The desktop status can contain a nominal quota before its payment state
    // has refreshed. Ask billing directly so the user sees the renewal dialog
    // instead of starting a job that the cloud will reject.
    if (status?.cloud_mode && status.signed_in) {
      try {
        const billing = await cloudBillingStatus();
        if (billing.ok && billing.billing?.needs_payment) {
          openPaywall();
          return;
        }
      } catch {
        // Do not block generation purely because a best-effort preflight check
        // is temporarily unavailable. The job-error fallback above still
        // presents the same dialog for a server-side quota denial.
      }
    }
    if (status?.cloud_mode && status.network_online === false) {
      setError(t("offline.generationFailed"));
      return;
    }
    beginJob();
    setError(null);
    setStatusLine(t("status.starting"));
    try {
      // A second start while a beat is running comes back with the running
      // job's id (backend single-flight), so this simply re-attaches to it.
      const handle = await startBeat(text, locale);
      let final: JobSnapshot;
      try {
        final = await pollJob(handle.job_id, onJobUpdate);
      } catch {
        setError(t("errors.jobLost"));
        setStatusLine(t("status.error"));
        return;
      }
      await finishBeatJob(final);
    } catch {
      setError(t("errors.genFailed"));
      setStatusLine(t("status.error"));
    } finally {
      endJob();
    }
  }

  // A spent quota / unpaid state is handled by the Account screen, which already
  // shows the purchase block ("Оформить доступ"). No separate dialog.
  function openPaywall() {
    setFlRedirectError(null);
    setView("account");
  }

  async function onOpenInFl(quiet = false) {
    if (busy) return;
    beginJob();
    setError(null);
    setStatusLine(t("status.openingFl"));
    try {
      const handle = await startOpenInFl();
      const final = await pollJob(handle.job_id);
      if (final.status === "error" && quiet) {
        setStatusLine(t("status.flError"));
      } else if (final.status === "error") {
        const message = jobErrorMessage(final, t);
        setError(message);
        if (resolveErrorType(final) === "fl_not_found") {
          setFlRedirectError(message);
          setView("settings");
        }
        setStatusLine(t("status.flError"));
      } else {
        const result = final.result as { message?: string } | null;
        setStatusLine(result?.message ?? t("status.flOpened"));
        await refreshStatus();
      }
    } catch {
      if (!quiet) setError(t("errors.somethingWentWrong"));
      setStatusLine(t("status.flError"));
    } finally {
      endJob();
    }
  }

  // "Random from library": pins one random file of the user's own library per
  // lane (808, kick, snare, clap, hats) until the next Random or Auto. A lane
  // with no usable file keeps its earlier choice and is named in the message.
  async function onRandomKit() {
    if (busy || importing || randomizing) return;
    setRandomizing(true);
    setError(null);
    try {
      const result = await randomKit();
      if (!result.ok) {
        setError(apiErrorMessage(result, t));
        return;
      }
      const list = Object.entries(result.picked ?? {})
        .map(([lane, name]) => `${t(`kit.roles.${lane}`)}: ${name}`)
        .join(" · ");
      if (list) toast(t("kit.randomDone", { list }), 9000);
      const kept = (result.kept ?? []).map((lane) => t(`kit.roles.${lane}`));
      if (kept.length) toast(t("kit.randomKept", { lanes: kept.join(", ") }), 9000);
      await refreshStatus();
    } catch {
      setError(t("errors.somethingWentWrong"));
    } finally {
      setRandomizing(false);
    }
  }

  async function onClearPinnedKit() {
    if (busy || importing || randomizing) return;
    setRandomizing(true);
    try {
      const result = await clearPinnedKit();
      if (!result.ok) {
        setError(apiErrorMessage(result, t));
        return;
      }
      await refreshStatus();
      toast(t("kit.autoDone"));
    } finally {
      setRandomizing(false);
    }
  }

  // "Clear folders": the backend MOVES the files into PLG_Archive/<date-time>
  // (never deletes) and rescans, so refreshStatus() then shows the new counters.
  async function onArchiveMaterial(targets: ArchiveTarget[]) {
    if (busy || importing) return;
    const result = await archiveMaterial(targets);
    if (!result.ok) throw new Error(apiErrorMessage(result, t));
    setError(null);
    await refreshStatus();
    toast(t("settings.archiveDone"));
  }

  async function importDroppedMaterials(files: DroppedMaterial[]) {
    if (!files.length || busy || importing) {
      if (!files.length) setError(t("home.heroUnsupported"));
      return;
    }
    setImporting(true);
    setError(null);
    setStatusLine(t("home.heroImporting"));
    let importId: string | undefined;
    try {
      const started = await beginMaterialImport();
      if (!started.ok || !started.import_id) throw new Error(started.error ?? t("common.error"));
      importId = started.import_id;

      for (const { file, path } of files) {
        for (let offset = 0; offset < file.size; offset += MATERIAL_CHUNK_BYTES) {
          const chunk = new Uint8Array(await file.slice(offset, offset + MATERIAL_CHUNK_BYTES).arrayBuffer());
          const result = await importMaterialChunk(importId, path, offset, base64FromBytes(chunk));
          if (!result.ok) throw new Error(result.error ?? t("common.error"));
        }
      }

      const completed = await finishMaterialImport(importId);
      importId = undefined;
      if (!completed.ok) throw new Error(completed.error ?? t("common.error"));
      await refreshStatus();
      setStatusLine(t("home.heroImported", { count: files.length }));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.error"));
    } finally {
      if (importId) await cancelMaterialImport(importId);
      setImporting(false);
    }
  }

  async function importFolderFallback() {
    if (busy || importing) return;
    setError(null);
    const picked = await pickFolder();
    if (!picked.ok || !picked.path) return;
    setImporting(true);
    setStatusLine(t("home.heroImporting"));
    try {
      const result = await importKitFolder(picked.path, false);
      if (!result.ok) throw new Error(apiErrorMessage(result, t));
      await refreshStatus();
      setStatusLine(t("home.heroImported", { count: typeof result.imported === "number" ? result.imported : 0 }));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.error"));
    } finally {
      setImporting(false);
    }
  }

  const quota = status?.quota;
  // ready() (see api.ts) waits for the pywebview bridge with no timeout of
  // its own — normally near-instant, but if the bridge never connects for
  // some reason, status would stay null forever and Home would be stuck on
  // "Loading…" permanently. Fall back to the pre-fix "add material" prompt
  // after a few seconds rather than hanging the screen indefinitely.
  const [statusLoadTimedOut, setStatusLoadTimedOut] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setStatusLoadTimedOut(true), 2500);
    return () => window.clearTimeout(timer);
  }, []);
  const statusLoaded = status !== null || statusLoadTimedOut;
  const materialCount = status?.material_total ?? 0;
  const hasMaterial = materialCount > 0;
  const hasMidi = (status?.library_midi_total ?? 0) > 0;
  const testerExpired = Boolean(status?.tester_expired);
  const canCreate = hasMaterial && (status?.library_audio_total ?? 0) > 0 && hasMidi && !busy && !importing && !testerExpired && !(status?.cloud_mode && status.network_online === false);
  const needsAuth = Boolean(status?.cloud_mode && !status?.signed_in);
  const networkOnline = status?.network_online !== false;
  // The offline banner already says "no connection" — don't repeat the same
  // fact as a second, local error box under the material panel. Keep the
  // local text only for the rare case this session's earlier fix targets:
  // the request failed but status hasn't (yet) confirmed the network is
  // down, so the banner isn't up to explain it.
  const bannerShown = Boolean(status?.cloud_mode && status.network_online === false);
  const isNetworkErrorText = error === t("errors.network") || error === t("offline.generationFailed");
  const testerExpiredText = testerExpired ? t("tester.expired", { date: status?.tester_expires ?? "" }) : null;
  const homeError = bannerShown && isNetworkErrorText ? null : (error ?? testerExpiredText);

  if (needsAuth) {
    return (
      <div className="shell shell--auth">
        <AuthView onAuthed={refreshStatus} />
        <Toaster />
      </div>
    );
  }

  return (
    <div className="shell shell--app">
      <Toaster />
      <SoundBrowser
        open={soundsOpen && view === "home" && hasMaterial}
        onClose={() => setSoundsOpen(false)}
        pinned={status?.pinned_kit ?? {}}
        disabled={busy || importing || randomizing}
        onChanged={refreshStatus}
        onBackToAuto={onClearPinnedKit}
      />
      <Sidebar
        view={view}
        onNavigate={(next) => {
          setFlRedirectError(null);
          setView(next);
        }}
        quota={quota}
        authEmail={status?.auth_email}
        hideAccount={Boolean(status?.tester_build)}
      />

      <div className="shell__stage">
        <div className="shell__main">
        <div className="shell__banners">
          {status?.tester_build ? (
            <div
              className={`dev-badge dev-badge--tester${testerExpired ? " dev-badge--expired" : ""}`}
              title={t(testerExpired ? "tester.expired" : "tester.badgeHint", { date: status.tester_expires ?? "", days: status.tester_days_left ?? 0 })}
            >
              {testerExpired ? "TESTER BUILD · EXPIRED" : "TESTER BUILD"}
            </div>
          ) : status?.offline_dev ? <div className="dev-badge" title="PLG_OFFLINE_DEV=1: no server, sign-in or quota (development only)">OFFLINE DEV</div> : null}
          <OfflineBanner
            online={networkOnline}
            cloudUrl={status?.cloud_url}
            cloudMode={status?.cloud_mode}
            retrying={retryingCloud}
            onRetry={retryCloudConnection}
          />
        </div>

        <main className={`shell__content ${view === "home" ? "shell__content--focus" : ""}`}>
          <ErrorBoundary key={view}>
            {view === "home" && (
              <HomeView
                onCreate={onCreate}
                busy={busy}
                canCreate={canCreate}
                error={homeError}
                statusLine={statusLine}
                hasMaterial={hasMaterial}
                audioCount={status?.library_audio_total ?? 0}
                midiCount={status?.library_midi_total ?? 0}
                statusLoaded={statusLoaded}
                importing={importing}
                onImport={importDroppedMaterials}
                onPickFolder={importFolderFallback}
                onRandomKit={onRandomKit}
                onOpenSounds={() => setSoundsOpen(true)}
                randomizing={randomizing}
              />
            )}
            {view === "account" && !status?.tester_build && (
              <AccountView
                onSaved={refreshStatus}
                cloudMode={status?.cloud_mode}
                authEmail={status?.auth_email}
                appVersion={status?.app_version}
              />
            )}
            {view === "settings" && (
              <SettingsView
                onSaved={refreshStatus}
                initialError={flRedirectError}
                onArchive={onArchiveMaterial}
                archiveDisabled={busy || importing}
                midiCount={status?.library_midi_total ?? 0}
                audioCount={status?.library_audio_total ?? 0}
              />
            )}
            {view === "help" && <HelpView />}
          </ErrorBoundary>
        </main>
      </div>
      </div>
    </div>
  );
}
