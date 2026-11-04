import { useEffect, useId, useRef, useState } from "react";
import { api } from "../lib/api";
import type { Place } from "../types";

interface Props {
  label: string;
  value: Place | null;
  onChange: (p: Place | null) => void;
  error?: string;
  placeholder?: string;
}

/** Accessible combobox. A place only counts once picked from the list, so
 *  ambiguous text like "Portland" can never be silently guessed. */
export function PlaceInput({ label, value, onChange, error, placeholder }: Props) {
  const id = useId();
  const [text, setText] = useState(value?.label ?? "");
  const [results, setResults] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => setText(value?.label ?? ""), [value]);

  useEffect(() => {
    if (value && text === value.label) return;
    const q = text.trim();
    if (q.length < 2) {
      setResults([]);
      return;
    }
    const t = setTimeout(() => {
      abort.current?.abort();
      const ctrl = new AbortController();
      abort.current = ctrl;
      setLoading(true);
      api
        .geocode(q, ctrl.signal)
        .then((r) => {
          setResults(r);
          setActive(0);
          setOpen(true);
          setLookupError(null);
        })
        .catch((e: Error) => {
          if (e.name !== "AbortError") setLookupError(e.message);
        })
        .finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(t);
  }, [text, value]);

  const pick = (p: Place) => {
    onChange(p);
    setText(p.label);
    setOpen(false);
  };

  const listId = `${id}-list`;
  const shownError = error ?? lookupError ?? undefined;
  return (
    <div className="field place">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        role="combobox"
        aria-expanded={open && results.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && results[active] ? `${listId}-${active}` : undefined}
        aria-invalid={!!shownError}
        autoComplete="off"
        placeholder={placeholder}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (value) onChange(null);
        }}
        onFocus={() => results.length && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onKeyDown={(e) => {
          if (!open || !results.length) return;
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => (a + 1) % results.length);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => (a - 1 + results.length) % results.length);
          } else if (e.key === "Enter") {
            e.preventDefault();
            pick(results[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {loading && <span className="field-spinner" aria-hidden />}
      {open && text.trim().length >= 2 && (
        <ul id={listId} role="listbox" className="suggestions">
          {results.length === 0 && !loading && <li className="empty">No matching places. Try a city and state.</li>}
          {results.map((p, i) => (
            <li
              key={`${p.lat},${p.lng}`}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(p);
              }}
              onMouseEnter={() => setActive(i)}
            >
              {p.label}
            </li>
          ))}
        </ul>
      )}
      {shownError && <p className="field-error">{shownError}</p>}
    </div>
  );
}
