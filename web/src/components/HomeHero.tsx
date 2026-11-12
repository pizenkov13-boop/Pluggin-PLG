import { type DragEvent, type KeyboardEvent } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowUpToLine, Dices, ListMusic } from "lucide-react";
import { useI18n } from "../i18n";
import { IconWarning } from "./icons";
import "./HomeHero.css";

export type DroppedMaterial = { file: File; path: string };

type Props = {
  onCreate: () => void;
  onPickFolder: () => Promise<void>;
  onRandomKit: () => Promise<void>;
  onOpenSounds: () => void;
  randomizing: boolean;
  busy: boolean;
  importing: boolean;
  dropActive: boolean;
  canCreate: boolean;
  error: string | null;
  hasMaterial: boolean;
  audioCount: number;
  midiCount: number;
  statusLoaded: boolean;
};

type DroppedEntry = {
  isFile: boolean;
  isDirectory: boolean;
  fullPath: string;
  file?: (callback: (file: File) => void, error?: () => void) => void;
  createReader?: () => { readEntries: (callback: (entries: DroppedEntry[]) => void, error?: () => void) => void };
};

type EntryItem = {
  webkitGetAsEntry?: () => DroppedEntry | null;
  getAsFile?: () => File | null;
};

const ENTRANCE = { type: "spring" as const, bounce: 0, duration: 0.48 };
const INTERACT = { type: "spring" as const, stiffness: 320, damping: 34, mass: 0.9 };

// Evaluated once when the desktop renderer starts, so the phrase stays stable
// while navigating through the app and changes only after the next launch.
const HERO_TITLES = [
  "LET'S MAKE A BEAT.",
  "WHAT ARE WE MAKING?",
  "READY WHEN YOU ARE.",
  "LET'S COOK.",
] as const;
const STARTUP_HERO_TITLE = HERO_TITLES[Math.floor(Math.random() * HERO_TITLES.length)];

const MATERIAL_EXTENSIONS = new Set(["mid", "midi", "wav", "mp3", "ogg", "flac", "aif", "aiff"]);

function isMaterialFile(file: File) {
  return MATERIAL_EXTENSIONS.has(file.name.split(".").pop()?.toLowerCase() ?? "");
}

function readAllEntries(reader: ReturnType<NonNullable<DroppedEntry["createReader"]>>): Promise<DroppedEntry[]> {
  return new Promise((resolve) => {
    const result: DroppedEntry[] = [];
    const read = () => reader.readEntries((entries) => {
      if (entries.length === 0) {
        resolve(result);
        return;
      }
      result.push(...entries);
      read();
    }, () => resolve(result));
    read();
  });
}

async function readEntry(entry: DroppedEntry): Promise<DroppedMaterial[]> {
  if (entry.isFile && entry.file) {
    return new Promise((resolve) => entry.file!(
      (file) => resolve(isMaterialFile(file) ? [{ file, path: entry.fullPath.replace(/^\/+/, "") || file.name }] : []),
      () => resolve([]),
    ));
  }
  if (!entry.isDirectory || !entry.createReader) return [];
  const children = await readAllEntries(entry.createReader());
  return (await Promise.all(children.map(readEntry))).flat();
}

export async function collectDroppedMaterials(event: DragEvent<HTMLElement>): Promise<DroppedMaterial[]> {
  const entries: DroppedEntry[] = [];
  const itemFiles: File[] = [];
  // WebView2 owns DataTransfer only for the synchronous drop event.  Keep a
  // local snapshot before awaiting directory traversal; otherwise a folder
  // drop can become an empty list by the time the fallback runs.
  const droppedFiles = Array.from(event.dataTransfer.files);
  for (const item of Array.from(event.dataTransfer.items)) {
    const typedItem = item as unknown as EntryItem;
    const entry = typedItem.webkitGetAsEntry?.();
    if (entry) entries.push(entry);
    else {
      const file = typedItem.getAsFile?.();
      if (file) itemFiles.push(file);
    }
  }
  const fromEntries = (await Promise.all(entries.map(readEntry))).flat();
  const fallback = fromEntries.length > 0
    ? fromEntries
    : (itemFiles.length > 0 ? itemFiles : droppedFiles)
      .filter(isMaterialFile)
      .map((file) => ({ file, path: file.webkitRelativePath || file.name }));
  const seen = new Set<string>();
  return fallback.filter(({ file, path }) => {
    const key = `${path}|${file.size}|${file.lastModified}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export default function HomeHero({
  onCreate,
  onPickFolder,
  onRandomKit,
  onOpenSounds,
  randomizing,
  busy,
  importing,
  dropActive,
  canCreate,
  error,
  hasMaterial,
  audioCount,
  midiCount,
  statusLoaded,
}: Props) {
  const { t } = useI18n();
  const reduce = useReducedMotion();
  const materialLabel = t("home.heroMaterialCount", { audio: audioCount, midi: midiCount });

  function onPanelKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (hasMaterial || busy || importing || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    void onPickFolder();
  }

  const panelText = importing
    ? t("home.heroImporting")
    : dropActive
      ? t("home.heroDropHere")
      : hasMaterial
        ? materialLabel
        // Before the first status reply, "no material" and "haven't checked
        // yet" both read as materialCount === 0 — without this, a kit that's
        // already on disk still flashes "ADD MIDI & DRUM KIT" every launch,
        // for as long as the boot resync (see plg_api.get_status) takes. Stay
        // blank rather than an explicit "Loading…" — that word reads as a
        // problem to a user who never sees it for more than an instant in a
        // real build; the panel just settles onto its final text.
        : !statusLoaded
          ? ""
          : t("home.heroEmpty");

  const enter = reduce
    ? {}
    : { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0 }, transition: ENTRANCE };

  return (
    <motion.section className="hero" {...enter}>
      <h1 className="hero__title">{STARTUP_HERO_TITLE}</h1>

      <div className="hero__slot">
        <motion.div
          className={`hero__panel${dropActive ? " hero__panel--dragging" : ""}`}
          initial={reduce ? false : { opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={INTERACT}
          role={hasMaterial ? undefined : "button"}
          tabIndex={hasMaterial ? undefined : 0}
          onClick={hasMaterial || busy || importing ? undefined : () => void onPickFolder()}
          onKeyDown={onPanelKeyDown}
        >
          <span className="hero__panel-text">{panelText}</span>
          {hasMaterial ? (
            <div className="hero__actions">
              <motion.button
                type="button"
                className="hero__send hero__sounds"
                onClick={onOpenSounds}
                disabled={busy || importing}
                aria-label={t("sounds.openAria")}
                title={t("sounds.openHint")}
                whileTap={reduce || busy || importing ? undefined : { scale: 0.94 }}
              >
                <ListMusic size={28} strokeWidth={2.25} aria-hidden />
              </motion.button>
              <motion.button
                type="button"
                className="hero__send hero__random"
                onClick={() => void onRandomKit()}
                disabled={busy || importing || randomizing}
                aria-label={t("kit.randomAria")}
                title={t("kit.randomHint")}
                whileTap={reduce || busy || importing || randomizing ? undefined : { scale: 0.94 }}
              >
                <Dices size={28} strokeWidth={2.25} aria-hidden />
              </motion.button>
              <motion.button
                type="button"
                className="hero__send"
                onClick={onCreate}
                disabled={!canCreate || busy || importing}
                aria-label={t("home.heroSendAria")}
                whileTap={reduce || !canCreate || busy || importing ? undefined : { scale: 0.94 }}
              >
                <ArrowUpToLine size={30} strokeWidth={2.25} aria-hidden />
              </motion.button>
            </div>
          ) : null}
        </motion.div>
      </div>

      {error && (
        <div className="hero__error" role="alert">
          <IconWarning />
          <span>{error}</span>
        </div>
      )}
    </motion.section>
  );
}
