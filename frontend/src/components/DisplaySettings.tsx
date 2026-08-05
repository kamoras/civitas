"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useDisplaySettings, type TextScale } from "@/hooks/useDisplaySettings";

const TEXT_SCALES: readonly { value: TextScale; label: string }[] = [
  { value: 100, label: "100%" },
  { value: 112, label: "112%" },
  { value: 125, label: "125%" },
  { value: 150, label: "150%" },
];

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  const id = useId();
  return (
    <div className="flex items-start gap-3">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`mt-0.5 shrink-0 w-10 h-5 border transition-colors ${
          checked ? "border-neon-cyan bg-neon-cyan/20" : "border-matrix-green/40 bg-transparent"
        }`}
      >
        <span
          aria-hidden="true"
          className={`block w-3.5 h-3.5 m-[2px] transition-transform ${
            checked ? "translate-x-[20px] bg-neon-cyan" : "translate-x-0 bg-matrix-green/60"
          }`}
        />
      </button>
      <label htmlFor={id} className="cursor-pointer">
        <span className="block text-sm text-matrix-green/90">{label}</span>
        <span className="block text-xs text-matrix-green/60 leading-snug">{hint}</span>
      </label>
    </div>
  );
}

export default function DisplaySettings() {
  const [settings, update] = useDisplaySettings();
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  const close = useCallback(() => {
    setOpen(false);
    buttonRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!panelRef.current?.contains(t) && !buttonRef.current?.contains(t)) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open, close]);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label="Display and readability settings"
        title="Display settings — font, contrast, text size"
        className="text-matrix-green/60 hover:text-neon-cyan font-mono text-xs tracking-widest transition-colors"
      >
        [AA]
      </button>

      {open && (
        <div
          ref={panelRef}
          id={panelId}
          role="dialog"
          aria-label="Display settings"
          className="absolute right-0 top-full mt-3 w-[19rem] max-w-[calc(100vw-2rem)] z-50
                     terminal-window p-5 space-y-5 bg-crt-black"
        >
          <div>
            <h2 className="text-neon-cyan font-mono text-xs tracking-widest mb-1">
              DISPLAY SETTINGS
            </h2>
            <p className="text-xs text-matrix-green/60 leading-snug">
              Saved to this browser. Tune these to your monitor and room lighting.
            </p>
          </div>

          <fieldset>
            <legend className="text-xs text-matrix-green/80 tracking-widest mb-2">TEXT SIZE</legend>
            <div className="flex gap-1.5">
              {TEXT_SCALES.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => update({ textScale: value })}
                  aria-pressed={settings.textScale === value}
                  className={`flex-1 py-1.5 text-xs font-mono border transition-colors ${
                    settings.textScale === value
                      ? "border-neon-cyan text-neon-cyan bg-neon-cyan/10"
                      : "border-matrix-green/30 text-matrix-green/70 hover:border-matrix-green/60"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend className="text-xs text-matrix-green/80 tracking-widest mb-2">THEME</legend>
            <div className="flex gap-1.5">
              {(
                [
                  ["dark", "TERMINAL"],
                  ["light", "DAYLIGHT"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => update({ theme: value })}
                  aria-pressed={settings.theme === value}
                  className={`flex-1 py-1.5 text-xs font-mono border transition-colors ${
                    settings.theme === value
                      ? "border-neon-cyan text-neon-cyan bg-neon-cyan/10"
                      : "border-matrix-green/30 text-matrix-green/70 hover:border-matrix-green/60"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="text-xs text-matrix-green/50 leading-snug mt-2">
              Daylight is easier to read in a bright room.
            </p>
          </fieldset>

          <div className="space-y-4 pt-1">
            <Toggle
              label="Legible type"
              hint="Replaces the pixel display font with a high x-height mono and lowers colour saturation."
              checked={settings.legible}
              onChange={(v) => update({ legible: v })}
            />
            <Toggle
              label="Visual effects"
              hint="Glow, CRT scanlines, and Matrix rain. Turn off if text looks blurred or shimmery."
              checked={settings.effects}
              onChange={(v) => update({ effects: v })}
            />
          </div>
        </div>
      )}
    </div>
  );
}
