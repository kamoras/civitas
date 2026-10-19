import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        /* The palette lives in src/app/globals.css as CSS custom properties,
           one block per theme (light "register paper" by default, dark
           "night register" by preference or the footer's choice). Every
           colour here points at one, so `/NN` opacity steps still work
           (Tailwind composites them with color-mix) and a theme is a set of
           values, not a second set of classes. Contrast for both themes is
           computed from globals.css by src/lib/wcagContrast.test.ts.

           `white` is redefined on purpose: the site's hundreds of
           `border-white/15` hairlines and `bg-white/[0.06]` washes are
           "a translucent rule in the ink's direction", which is ink on paper
           and white on carbon (--overlay). */
        white: "var(--overlay)",
        ink: {
          hi: "var(--ink-hi)", // headings, figures, emphasis
          DEFAULT: "var(--ink)", // body prose
          lo: "var(--ink-lo)", // secondary, captions
          min: "var(--ink-min)", // FLOOR. Nothing dimmer ships.
        },
        // Ledger green: links, live data, focus. Never a fill behind prose.
        phos: {
          DEFAULT: "var(--phos)",
          mid: "var(--phos-mid)",
        },
        signal: {
          cyan: "var(--signal-cyan)",
          amber: "var(--signal-amber)",
          orange: "var(--signal-orange)",
          red: "var(--signal-red)",
          magenta: "var(--signal-magenta)",
        },
        // Elevation: the page, a box on it, a wash. Depth is rule weight.
        surface: {
          base: "var(--surface-base)",
          DEFAULT: "var(--surface)",
          raised: "var(--surface-raised)",
        },
        "dem-blue": "var(--dem-blue)",
        "rep-red": "var(--rep-red)",
        "ind-purple": "var(--ind-purple)",
      },
      fontFamily: {
        /* One job each (src/app/fonts.ts has the faces and why):
             display   Source Serif 4: page and section titles
             sans      Fira Sans: body, UI, figures (tabular, set on body)
             mono      Fira Sans as well. `font-mono` was Share Tech Mono
                       and still marks labels, data and status across the
                       site; those keep their class and lose the monospace,
                       which was the main source of the "technical" feel.
             id        DM Mono: record identifiers only (issue ids, roll-call
                       and bill numbers, filing ids). Never prose.
             wordmark  Archivo 800: the CIVITAS mark and nothing else. */
        display: ["var(--font-serif)", "Georgia", "serif"],
        sans: ["var(--font-fira)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-fira)", "ui-sans-serif", "system-ui", "sans-serif"],
        id: ["var(--font-dm-mono)", "ui-monospace", "monospace"],
        wordmark: ["var(--font-archivo)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      borderWidth: {
        // Three rule weights carry the entire hierarchy, the way a printed
        // form does: 3px accent for a section, 3px neutral for a block,
        // 1px hairline for an item. Tailwind ships 2 and 4 but not 3.
        3: "3px",
      },
      animation: {
        glitch: "glitch 1s infinite",
        "glitch-skew": "glitch-skew 1s infinite",
        scanline: "scanline 8s linear infinite",
        flicker: "flicker 0.15s infinite",
        marquee: "marquee 30s linear infinite",
        "pulse-neon": "pulse-neon 1.5s ease-in-out infinite alternate",
        "fade-in-up": "fade-in-up 0.6s ease-out",
        blink: "blink 1s step-end infinite",
      },
      keyframes: {
        glitch: {
          "0%, 100%": {
            transform: "translate(0)",
            textShadow: "-2px 0 #ff00ff, 2px 0 #00ffff",
          },
          "25%": {
            transform: "translate(-2px, 1px)",
            textShadow: "2px 0 #ff00ff, -2px 0 #00ffff",
          },
          "50%": {
            transform: "translate(1px, -1px)",
            textShadow: "-1px 0 #ff00ff, 1px 0 #00ffff",
          },
          "75%": {
            transform: "translate(2px, 1px)",
            textShadow: "1px 0 #ff00ff, -1px 0 #00ffff",
          },
        },
        "glitch-skew": {
          "0%, 100%": { transform: "skew(0deg)" },
          "20%": { transform: "skew(-2deg)" },
          "40%": { transform: "skew(3deg)" },
          "60%": { transform: "skew(-1deg)" },
          "80%": { transform: "skew(2deg)" },
        },
        scanline: {
          "0%": { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(100vh)" },
        },
        flicker: {
          "0%": { opacity: "0.97" },
          "50%": { opacity: "1" },
          "100%": { opacity: "0.98" },
        },
        // The rendered text is two back-to-back copies of the same content
        // (see Marquee.tsx's `doubled`) so a -50% shift — exactly one
        // copy's width — lands the second copy precisely where the first
        // started, looping seamlessly. The previous 100% -> -100% slid the
        // whole (already-doubled, so extra-wide) block fully off both
        // edges each cycle, which read as a visible snap back at the loop
        // boundary instead of a continuous scroll.
        marquee: {
          "0%": { transform: "translateX(0%)" },
          "100%": { transform: "translateX(-50%)" },
        },
        "pulse-neon": {
          "0%": {
            textShadow: "0 0 7px #00ff41, 0 0 10px #00ff41, 0 0 21px #00ff41",
          },
          "100%": {
            textShadow: "0 0 14px #00ff41, 0 0 20px #00ff41, 0 0 42px #00ff41",
          },
        },
        "fade-in-up": {
          "0%": { opacity: "0", transform: "translateY(20px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        blink: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0" },
        },
      },
    },
  },
  plugins: [],
};
export default config;
