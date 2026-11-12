/** One shared <audio> element for the sound browser's preview.

    A media element answers a new `src` by rejecting the PREVIOUS `play()` promise with an AbortError
    ("interrupted by a call to pause()"). Reading that rejection as a failure made a quick second click
    show "cannot be played" and clear the playing mark while the new sound was in fact playing. Every
    request therefore takes a ticket and only the latest one may report a failure. */
export type PlayerEvents = {
  /** The file finished on its own. */
  onEnded: () => void;
  /** The LATEST file could not be played (a superseded click never reports). */
  onFailed: () => void;
};

export class SoundPlayer {
  private readonly audio = new Audio();
  private ticket = 0;
  private reported = 0;

  constructor(private readonly events: PlayerEvents) {
    this.audio.addEventListener("ended", () => this.events.onEnded());
    this.audio.addEventListener("error", () => {
      // stop() empties the source on purpose, which is not an error worth showing.
      if (this.audio.getAttribute("src")) this.fail(this.ticket);
    });
  }

  play(url: string): void {
    const ticket = ++this.ticket;
    this.audio.pause();
    this.audio.src = url;
    this.audio.play().catch((error: unknown) => {
      if (ticket !== this.ticket) return;
      if (error instanceof DOMException && error.name === "AbortError") return;
      this.fail(ticket);
    });
  }

  /** A broken file raises both a rejected play() and an error event: tell the UI once per click. */
  private fail(ticket: number): void {
    if (this.reported === ticket) return;
    this.reported = ticket;
    this.events.onFailed();
  }

  stop(): void {
    this.ticket += 1;
    this.audio.pause();
    this.audio.removeAttribute("src");
    this.audio.load();
  }
}
