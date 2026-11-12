import { useCallback, useEffect, useState } from "react";
import { getProducerBlueprint } from "../api";
import { useI18n } from "../i18n";
import "./BlueprintChecklist.css";

const STORAGE_KEY = "plg-blueprint-done";

type Step = { id: string; text: string };

type Props = {
  beatReady: boolean;
  sessionKey: string;
};

function loadDone(sessionKey: string): Set<string> {
  try {
    const raw = localStorage.getItem(`${STORAGE_KEY}:${sessionKey}`);
    if (!raw) return new Set();
    return new Set(JSON.parse(raw) as string[]);
  } catch {
    return new Set();
  }
}

function saveDone(sessionKey: string, done: Set<string>) {
  localStorage.setItem(`${STORAGE_KEY}:${sessionKey}`, JSON.stringify([...done]));
}

export default function BlueprintChecklist({ beatReady, sessionKey }: Props) {
  const { t, locale } = useI18n();
  const [steps, setSteps] = useState<Step[]>([]);
  const [done, setDone] = useState<Set<string>>(() => loadDone(sessionKey));

  const refresh = useCallback(async () => {
    if (!beatReady) {
      setSteps([]);
      return;
    }
    const data = await getProducerBlueprint();
    if (data.ok && Array.isArray(data.steps)) {
      setSteps(data.steps as Step[]);
    }
  }, [beatReady, locale]);

  useEffect(() => {
    setDone(loadDone(sessionKey));
    refresh();
  }, [sessionKey, refresh]);

  function toggle(id: string) {
    setDone((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      saveDone(sessionKey, next);
      return next;
    });
  }

  if (!beatReady || steps.length === 0) return null;

  const completed = steps.filter((s) => done.has(s.id)).length;

  return (
    <section className="blueprint">
      <div className="blueprint__head">
        <h3>{t("blueprint.title")}</h3>
        <span className="blueprint__count">
          {completed}/{steps.length}
        </span>
      </div>
      <ul className="blueprint__list">
        {steps.map((step, index) => {
          const checked = done.has(step.id);
          return (
            <li key={step.id} className={checked ? "blueprint__item--done" : ""}>
              <button type="button" className="blueprint__row" onClick={() => toggle(step.id)}>
                <span className="blueprint__num">{index + 1}</span>
                <span className="blueprint__text">{step.text}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
