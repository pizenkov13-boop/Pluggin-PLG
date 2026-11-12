import type { PromptIdea } from "../i18n";
import "./SlashMenu.css";

type Props = {
  items: PromptIdea[];
  highlightIndex: number;
  onHover: (index: number) => void;
  onSelect: (idea: PromptIdea) => void;
};

export default function SlashMenu({ items, highlightIndex, onHover, onSelect }: Props) {
  return (
    <ul className="slash-menu" role="listbox">
      {items.map((idea, i) => (
        <li key={idea.id}>
          <button
            type="button"
            role="option"
            aria-selected={i === highlightIndex}
            className={`slash-menu__item ${i === highlightIndex ? "slash-menu__item--active" : ""}`}
            onMouseEnter={() => onHover(i)}
            onClick={() => onSelect(idea)}
          >
            {idea.title}
          </button>
        </li>
      ))}
    </ul>
  );
}
