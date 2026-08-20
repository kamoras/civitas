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
        /* ── Public-records palette ──────────────────────────────────────
           Every value below is a SOLID hex, deliberately: the `/NN` opacity
           steps these replace are the root cause of the 260-line contrast
           clamp block in globals.css. Alpha composited over the page
           background is invisible to a linter and easy to write too dim, so
           the ramp stops at a measured floor and there is nothing dimmer to
           reach for.

           Quoted in APCA Lc first, then WCAG 2.2 — because a reader told us
           the grey was hard to read and WCAG said it was fine.

           WCAG 2.x is polarity-blind and systematically overrates light text
           on dark: the old ramp cleared its 4.5:1 floor everywhere and still
           put 61 elements on the homepage at Lc 35, below APCA's hard floor
           of 45 for any meaningful text, with secondary prose at 48 against a
           body target of 75. Lc is the number to design to in dark mode; the
           ratio is kept alongside it because that is what axe and the law
           still measure. Targets: 90+ fluent body, 75 secondary, 60 the floor
           for short non-fluent labels.

           Both are computed rather than estimated, and quoted against the
           LIGHTEST background any text in the app is actually composited on — which is not `surface.raised`
           (#191512) but #262010, a 10% amber wash sitting on top of it. That
           distinction cost two tokens: measured against `raised` the floor
           read 4.74:1 and axe still failed it, because a tinted badge is a
           surface too. Measure the composite, not the token. Anything added must clear
           4.5:1 there. Re-measured in a browser after the ground was warmed;
           the whole ramp moved by roughly a tenth and nothing crossed the
           floor.

           This is the whole palette. The legacy matrix-green, neon-cyan,
           neon-pink, neon-yellow, terminal-bg, terminal-border and crt-black
           tokens that used to sit below it are gone: the migration finished
           with zero call sites left on any of them, the clamp block that
           protected them was deleted with it, and a dead token left in the
           config is just an invitation to reach for #00ffff again. */

        // Prose and UI text. Warm neutral — this is INK, and the register
        // it belongs to is a printed one.
        //
        // It used to be "faintly green-cast to sit on the phosphor
        // background". That cast is the single most legible tell that the
        // page is pretending to be a CRT rather than a document: green text
        // on green-black is a terminal, and a terminal is the stock costume
        // for "technical", not a decision anyone made about public records.
        // Same luminance ramp, warmed off the phosphor axis.
        ink: {
          // APCA Lc against `surface.raised`, with the WCAG 2.x ratio after it.
          // Lc is the one that matters here and the reason this ramp moved.
          hi: "#F2EEE7", // Lc 96 · 15.69:1 — headings, figures, emphasis
          DEFAULT: "#E3DCD1", // Lc 85 · 13.33:1 — body prose
          lo: "#CDC7BC", // Lc 72 · 10.79:1 — secondary, captions
          min: "#BBB5AC", // Lc 62 ·  8.92:1 — FLOOR. Nothing dimmer ships.
        },

        // Phosphor. STATUS ONLY now, not structure.
        //
        // It was "data and wayfinding — status, rules, figures, link
        // underlines", and in practice that put two full-width 3px green
        // rules on every page (the records band's edge and every masthead's
        // underline) plus a green scrollbar. Structure drawn in phosphor is
        // what made the site read as a terminal no matter what the words
        // said. Rules are ink now; green is reserved for something that is
        // currently true about the data — a run that completed, a live
        // figure, a score. Never a fill behind a call to action.
        phos: {
          DEFAULT: "#00FF41", // 13.29:1
          mid: "#04B831", //  6.83:1
          dim: "#059A2A", //  9.74:1
        },

        // Score tiers and categorical tags. `magenta` doubles as the second
        // print plate in the overprint wordmark.
        //
        // The party hues below are lighter than a party's "real" colour on
        // purpose. A saturated red or blue is inherently low-luminance, and
        // these are set at 12px inside a tinted badge — the worst case in the
        // app. At their old values that badge measured Lc 44, under APCA's
        // hard floor, while passing WCAG at 6:1. Lightening to Lc 56 keeps
        // them recognisably red/blue/purple; taking them to Lc 60 turned red
        // into pink, which costs more legibility (of the party) than it buys.
        signal: {
          cyan: "#4DE3E8", // 11.64:1
          amber: "#FFD84D", // 13.12:1
          orange: "#FF8A3D", //  7.74:1
          red: "#FF8989", // Lc 56 ·  9.02:1
          magenta: "#FF6BD6", //  7.19:1
        },

        // Elevation. Three steps is the whole set; depth is carried by rule
        // weight, not by shadow.
        //
        // Warm, not magenta-cast. The old #0D0208 was a CRT black with a
        // violet bias — the ground half of the same costume as the green
        // ink above. These are the colour of an archive photographed in low
        // light: still near-black, so the measured ratios barely move, but
        // the page stops reading as a screen pretending to glow.
        surface: {
          base: "#0E0C0A",
          DEFAULT: "#14110E",
          raised: "#191512",
        },

        "dem-blue": "#82acff", // Lc 56 ·  9.06:1 — was #0066ff (4.22:1), which failed even at full opacity
        "rep-red": "#ff8989", // Lc 56 ·  9.02:1 — alias of signal.red — matches signal.red; kept as an alias for party call sites
        "ind-purple": "#c995ff", // Lc 56 ·  9.06:1 — lightened twice; see the composite note above
      },
      fontFamily: {
        /* Three faces, one job each. `display` and `sans` are both Archivo:
           a grotesque reads broadsheet where a bookish serif reads endowment,
           and prose does not need a fourth family to be legible.

           `mono` (Share Tech Mono) is data, IDs, timestamps, labels and
           status — the terminal voice, now meaning something because it is
           no longer also the body face.

           `pixel` (Press Start 2P) is the wordmark and nothing else. It is a
           bitmap face on a 125/1000em grid, so it only rasterises cleanly at
           multiples of 8px; at label sizes it was off-grid at ~86% of its
           call sites, which is why globals.css substitutes it out below 12px.

           There is no fourth family. `terminal` (VT323) was removed along with
           its font import: it had no call sites left, and a 0.400em x-height
           that rendered ~5.6px lowercase at text-sm is not a face to leave
           within reach. */
        display: ["var(--font-archivo)", "ui-sans-serif", "system-ui", "sans-serif"],
        sans: ["var(--font-archivo)", "ui-sans-serif", "system-ui", "sans-serif"],
        pixel: ["var(--font-press-start)", "monospace"],
        mono: ["var(--font-share-tech)", "monospace"],
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
