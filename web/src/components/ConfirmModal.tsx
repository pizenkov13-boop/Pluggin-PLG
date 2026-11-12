import { useEffect, useRef, type ReactNode } from "react";
import "./ConfirmModal.css";

type Props = {
  open: boolean;
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  /** Style the confirm button as a destructive action (account deletion etc.). */
  destructive?: boolean;
  busy?: boolean;
  /** Extra content between the message and the buttons (a choice, a list). */
  children?: ReactNode;
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

/** In-app confirmation dialog in the PLG design — replaces window.confirm(),
    which the pywebview shell renders as an OS dialog titled with the local
    "127.0.0.1:<port>" origin. Overlay click and Esc both cancel; the Cancel
    button takes focus on open so a stray Enter never confirms. */
export default function ConfirmModal({
  open,
  title,
  message,
  confirmLabel,
  cancelLabel,
  destructive,
  busy,
  children,
  confirmDisabled,
  onConfirm,
  onCancel,
}: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  return (
    <div
      className="confirm-modal"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div
        className="confirm-modal__card"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-modal-title"
        aria-describedby="confirm-modal-msg"
      >
        <h2 className="confirm-modal__title" id="confirm-modal-title">
          {title}
        </h2>
        <p className="confirm-modal__msg" id="confirm-modal-msg">
          {message}
        </p>
        {children}
        <div className="confirm-modal__actions">
          <button
            type="button"
            className="confirm-modal__btn"
            onClick={onCancel}
            disabled={busy}
            ref={cancelRef}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={`confirm-modal__btn confirm-modal__btn--primary ${
              destructive ? "confirm-modal__btn--danger" : ""
            }`}
            onClick={onConfirm}
            disabled={busy || confirmDisabled}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
