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

           Ratios are WCAG 2.2 relative-luminance contrast against the page
           background (#0D0208), computed rather than estimated. Anything
           added here must clear 4.5:1 on that background AND on `surface`
           (#140A0F), which is the lightest thing text sits on.

           This is the whole palette. The legacy matrix-green, neon-cyan,
           neon-pink, neon-yellow, terminal-bg, terminal-border and crt-black
           tokens that used to sit below it are gone: the migration finished
           with zero call sites left on any of them, the clamp block that
           protected them was deleted with it, and a dead token left in the
           config is just an invitation to reach for #00ffff again. */

        // Prose and UI text. Neutral, faintly green-cast to sit on the
        // phosphor background without vibrating against it.
        ink: {
          hi: "#EAF3EC", // 18.00:1 — headings, figures, emphasis
          DEFAULT: "#B4C2B7", // 11.02:1 — body prose
          lo: "#93A296", //  7.63:1 — secondary, captions
          min: "#7C8B7F", //  5.69:1 — FLOOR. Nothing dimmer ships.
        },

        // Phosphor. Data and wayfinding only — status, rules, figures, link
        // underlines. Never a fill behind a call to action.
        phos: {
          DEFAULT: "#00FF41", // 14.94:1
          mid: "#04B831", //  7.68:1
          dim: "#059A2A", //  5.50:1 — FLOOR
        },

        // Score tiers and categorical tags. `magenta` doubles as the second
        // print plate in the overprint wordmark and the stamp rule.
        signal: {
          cyan: "#4DE3E8", // 13.09:1
          amber: "#FFD84D", // 14.75:1
          orange: "#FF8A3D", //  8.70:1
          red: "#FF5C5C", //  6.74:1
          magenta: "#FF6BD6", //  8.09:1
        },

        // Elevation. Three steps is the whole set; depth is carried by rule
        // weight, not by shadow.
        surface: {
          base: "#0D0208",
          DEFAULT: "#140A0F",
          raised: "#190F14",
        },

        "dem-blue": "#6699ff", //  7.35:1 — was #0066ff (4.22:1), which failed even at full opacity
        "rep-red": "#ff5c5c", //  6.74:1 — matches signal.red; kept as an alias for party call sites
        "ind-purple": "#ac56ff", //  5.37:1 — was #9933ff (4.15:1), which failed even at full opacity
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
