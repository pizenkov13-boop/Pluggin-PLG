import { useSyncExternalStore } from "react";
import "./Toast.css";

export type ToastVariant = "default" | "success" | "error";

interface ToastItem {
  id: number;
  message: string;
  variant: ToastVariant;
  duration: number;
}

let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function dismiss(id: number) {
  items = items.filter((item) => item.id !== id);
  emit();
}

function push(message: string, variant: ToastVariant, duration: number) {
  const id = nextId++;
  items = [...items, { id, message, variant, duration }];
  emit();
  window.setTimeout(() => dismiss(id), duration);
  return id;
}

/** Global toast API — call from anywhere, no provider/context needed. */
export const toast = Object.assign(
  (message: string, duration = 4000) => push(message, "default", duration),
  {
    success: (message: string, duration = 4000) => push(message, "success", duration),
    error: (message: string, duration = 6000) => push(message, "error", duration),
    dismiss,
  },
);

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return items;
}

/** Mount once near the root — <Toaster /> in App.tsx. */
export default function Toaster() {
  const list = useSyncExternalStore(subscribe, getSnapshot);

  if (list.length === 0) return null;

  return (
    <div className="toaster" role="status" aria-live="polite">
      {list.map((item) => (
        <div key={item.id} className={`toast toast--${item.variant}`}>
          <span className="toast__dot" />
          <p className="toast__message">{item.message}</p>
          <button
            type="button"
            className="toast__close"
            aria-label="Dismiss"
            onClick={() => dismiss(item.id)}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
