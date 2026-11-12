import { useRef, useState, type DragEvent } from "react";
import GeneratingState from "./GeneratingState";
import HomeHero, { collectDroppedMaterials, type DroppedMaterial } from "./HomeHero";
import "./HomeView.css";

type Props = {
  onCreate: () => void;
  busy: boolean;
  canCreate: boolean;
  error: string | null;
  statusLine: string;
  hasMaterial: boolean;
  audioCount: number;
  midiCount: number;
  statusLoaded: boolean;
  importing: boolean;
  onImport: (files: DroppedMaterial[]) => Promise<void>;
  onPickFolder: () => Promise<void>;
  onRandomKit: () => Promise<void>;
  onOpenSounds: () => void;
  randomizing: boolean;
};

export default function HomeView({
  onCreate,
  busy,
  canCreate,
  error,
  statusLine,
  hasMaterial,
  audioCount,
  midiCount,
  statusLoaded,
  importing,
  onImport,
  onPickFolder,
  onRandomKit,
  onOpenSounds,
  randomizing,
}: Props) {
  const [dropActive, setDropActive] = useState(false);
  const dragDepth = useRef(0);

  function resetDropState() {
    dragDepth.current = 0;
    setDropActive(false);
  }

  function onDragEnter(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (busy || importing || !event.dataTransfer.types.includes("Files")) return;
    dragDepth.current += 1;
    setDropActive(true);
  }

  function onDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (busy || importing) return;
    dragDepth.current -= 1;
    if (dragDepth.current <= 0) resetDropState();
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    if (!busy && !importing) event.preventDefault();
  }

  async function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    resetDropState();
    if (busy || importing) return;
    const materials = await collectDroppedMaterials(event);
    await onImport(materials);
  }

  // After generation the hero stays on screen (Figma 1:13) — no Beat Ready /
  // ratings / Open in FL / Regenerate dock. FL opens from the create flow.
  return (
    <div
      className={`home${dropActive ? " home--dropping" : ""}`}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      {busy ? (
        <GeneratingState detail={statusLine} />
      ) : (
        <HomeHero
          onCreate={onCreate}
          busy={busy}
          canCreate={canCreate}
          error={error}
          hasMaterial={hasMaterial}
          audioCount={audioCount}
          midiCount={midiCount}
          statusLoaded={statusLoaded}
          importing={importing}
          dropActive={dropActive}
          onPickFolder={onPickFolder}
          onRandomKit={onRandomKit}
          onOpenSounds={onOpenSounds}
          randomizing={randomizing}
        />
      )}
    </div>
  );
}
