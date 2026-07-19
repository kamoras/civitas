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
        "matrix-green": "#00ff41",
        "matrix-dark-green": "#003b00",
        "neon-cyan": "#00ffff",
        "neon-pink": "#ff00ff",
        "neon-yellow": "#ffff00",
        "terminal-bg": "#0a0a0a",
        "terminal-border": "#333333",
        "crt-black": "#0d0208",
        "dem-blue": "#0066ff",
        "rep-red": "#ff3333",
        "ind-purple": "#9933ff",
      },
      fontFamily: {
        terminal: ["var(--font-vt323)", "monospace"],
        pixel: ["var(--font-press-start)", "monospace"],
        mono: ["var(--font-share-tech)", "monospace"],
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
