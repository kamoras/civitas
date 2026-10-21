"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { fetchSearchSuggestions } from "@/lib/api";
import type { SearchItem } from "@/types/search";

// Wait this long after the last keystroke before asking: a word typed at
// speed is one request, not one per letter.
const DEBOUNCE_MS = 150;
// One letter matches half the site.
const MIN_CHARS = 2;

// The highlighted match is drawn inverted (ink ground, dark text), a
// change of fill rather than of colour alone.
const OPTION_ACTIVE = "bg-ink-hi text-surface-base";
const OPTION = "text-ink-hi";

/** What the live region says for a set of suggestions. */
export function suggestionStatus(items: SearchItem[], preselected: boolean, query: string): string {
  if (query.trim().length < MIN_CHARS) return "";
  if (items.length === 0)
    return `Nothing on file matching ${query.trim()}. Press Enter to search anyway.`;
  const n = `${items.length} ${items.length === 1 ? "match" : "matches"}.`;
  return preselected
    ? `${n} Best match: ${items[0].label}, ${items[0].group}. Press Enter to open it.`
    : `${n} Use the up and down arrows to choose one.`;
}

/**
 * Site search with autocomplete: the WAI-ARIA 1.2 combobox pattern (an
 * editable combobox with a listbox popup and list autocomplete). The input
 * is `role="combobox"`, controls the listbox and points at the highlighted
 * option with `aria-activedescendant`, so focus never leaves the input; a
 * polite live region says how many matches there are.
 *
 * Keys: Down and Up move through the matches (and open the list), Enter
 * opens the highlighted match or, with none highlighted, submits the form,
 * Escape closes the list and then clears the input, Tab leaves. The first
 * match is highlighted only when the search would go straight to it anyway
 * (the API's `direct`), so Enter there lands on the page in one step.
 *
 * It is a plain GET form to /search underneath: without JavaScript, or when
 * Enter is pressed with nothing highlighted, the server decides (straight to
 * the page, or the results).
 */
export default function SearchCombobox({
  initialQuery = "",
  autoFocus = false,
  onNavigate,
  label = "Search Civitas",
  inlineList = false,
  hidden,
}: {
  initialQuery?: string;
  autoFocus?: boolean;
  /** Called before a suggestion is opened (the dialog closes itself). */
  onNavigate?: () => void;
  label?: string;
  /** The list in the page's flow (inside the search dialog) rather than
   *  over what follows it. */
  inlineList?: boolean;
  /** Fields the form sends beside the words: the results page's filter
   *  (`tab`), and on the Documents filter its other filters, so a new
   *  search keeps them. */
  hidden?: Record<string, string>;
}) {
  const router = useRouter();
  const id = useId();
  const inputId = `${id}-input`;
  const listId = `${id}-list`;
  const [value, setValue] = useState(initialQuery);
  const [items, setItems] = useState<SearchItem[]>([]);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState("");
  const typed = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  useEffect(() => {
    // Only what the reader typed asks for suggestions: a query the page
    // was opened with is already answered by the results below it.
    if (!typed.current) return;
    const q = value.trim();
    if (q.length < MIN_CHARS) {
      setItems([]);
      setOpen(false);
      setActive(-1);
      setStatus("");
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      fetchSearchSuggestions(q, controller.signal)
        .then((data) => {
          const preselect = data.direct !== null && data.items[0]?.url === data.direct;
          setItems(data.items);
          setActive(preselect ? 0 : -1);
          setOpen(data.items.length > 0);
          setStatus(
            data.unavailable
              ? "Search is starting up. Press Enter to search Explore instead."
              : suggestionStatus(data.items, preselect, q)
          );
        })
        .catch((err: unknown) => {
          if ((err as Error)?.name !== "AbortError") {
            setItems([]);
            setOpen(false);
            setStatus("Suggestions are unavailable. Press Enter to search.");
          }
        });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [value]);

  function go(item: SearchItem) {
    setOpen(false);
    onNavigate?.();
    router.push(item.url);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    const n = items.length;
    if (e.key === "ArrowDown") {
      if (n === 0) return;
      e.preventDefault();
      if (!open) {
        setOpen(true);
        setActive(active >= 0 ? active : 0);
      } else setActive((active + 1) % n);
    } else if (e.key === "ArrowUp") {
      if (n === 0) return;
      e.preventDefault();
      if (!open) {
        setOpen(true);
        setActive(active >= 0 ? active : n - 1);
      } else setActive(active <= 0 ? n - 1 : active - 1);
    } else if (e.key === "Enter") {
      if (open && active >= 0 && items[active]) {
        e.preventDefault();
        go(items[active]);
      }
    } else if (e.key === "Escape") {
      // Stopped here so a dialog around the combobox doesn't also close:
      // the first Escape closes the list, the second clears the text, the
      // third (nothing left to undo) is the dialog's.
      if (open) {
        e.preventDefault();
        setOpen(false);
        setActive(-1);
      } else if (value) {
        e.preventDefault();
        typed.current = true;
        setValue("");
      }
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  }

  const activeId = open && active >= 0 ? `${id}-opt-${active}` : undefined;

  // aria-activedescendant moves no focus, so nothing scrolls the
  // highlighted match into view by itself: at a large text size on a
  // phone the list is taller than the screen.
  useEffect(() => {
    if (activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: "nearest" });
  }, [activeId]);

  return (
    <form role="search" action="/search" method="get" className="@container relative w-full">
      {Object.entries(hidden ?? {}).map(([name, v]) => (
        <input key={name} type="hidden" name={name} value={v} />
      ))}
      <label htmlFor={inputId} className="mb-2 block font-mono text-xs text-ink-lo">
        {label}
      </label>
      {/* GO under the field in a narrow box (a 320 px phone, or a doubled
          text size), beside it from 20rem: side by side there, the field
          was left a few characters wide (WCAG 1.4.10). */}
      <div className="flex flex-wrap items-stretch border-2 border-ink-lo bg-surface-base focus-within:border-ink-hi @[20rem]:flex-nowrap">
        <span aria-hidden="true" className="flex items-center pl-3 font-mono text-sm text-ink-lo">
          &gt;
        </span>
        <input
          ref={inputRef}
          id={inputId}
          name="q"
          type="text"
          inputMode="search"
          enterKeyHint="search"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={activeId}
          aria-describedby={`${id}-hint`}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          maxLength={300}
          value={value}
          onChange={(e) => {
            typed.current = true;
            setValue(e.target.value);
          }}
          onKeyDown={onKeyDown}
          onBlur={() => setOpen(false)}
          onFocus={() => items.length > 0 && value.trim().length >= MIN_CHARS && setOpen(true)}
          placeholder="A member, bill, agency, district, page…"
          className="min-h-11 min-w-0 flex-1 basis-0 bg-transparent px-2 font-mono text-base text-ink-hi caret-phos placeholder:text-ink-min"
        />
        <button
          type="submit"
          className="min-h-11 w-full border-t-2 border-ink-lo px-3 font-mono text-xs text-ink-hi hover:bg-white/10 @[20rem]:w-auto @[20rem]:min-w-16 @[20rem]:border-t-0 @[20rem]:border-l-2"
        >
          Go
        </button>
      </div>
      <p id={`${id}-hint`} className="mt-2 font-mono text-xs text-ink-min">
        Names, bill numbers (HR 1), districts (TX-23), agencies (EPA), FR citations. Enter on an
        exact match goes straight there.
      </p>
      <ul
        id={listId}
        role="listbox"
        aria-label="Matches"
        hidden={!open}
        className={`${inlineList ? "" : "absolute left-0 right-0 z-20 max-h-[min(28rem,60vh)] overflow-y-auto"} mt-1 border-2 border-ink-hi bg-surface-base`}
      >
        {items.map((item, i) => (
          <li
            key={`${item.url}-${i}`}
            id={`${id}-opt-${i}`}
            role="option"
            aria-selected={i === active}
            // mousedown, not click: the input's blur would close the list
            // before a click landed.
            onMouseDown={(e) => {
              e.preventDefault();
              go(item);
            }}
            onMouseMove={() => i !== active && setActive(i)}
            className={`flex min-h-11 cursor-pointer flex-col justify-center gap-0.5 border-b border-white/15 px-3 py-2 last:border-b-0 ${i === active ? OPTION_ACTIVE : OPTION}`}
          >
            <span className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 font-sans text-sm font-semibold">{item.label}</span>
              <span
                className={`shrink-0 font-mono text-[11px] ${
                  i === active ? "text-surface-base" : "text-ink-lo"
                }`}
              >
                {item.group}
              </span>
            </span>
            {item.context && (
              <span
                className={`font-mono text-xs ${i === active ? "text-surface-base" : "text-ink-min"}`}
              >
                {item.context}
              </span>
            )}
          </li>
        ))}
      </ul>
      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {status}
      </div>
    </form>
  );
}
