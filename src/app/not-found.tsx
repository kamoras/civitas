import Link from "next/link";
import MatrixRain from "@/components/effects/MatrixRain";
import GlitchText from "@/components/effects/GlitchText";

export default function NotFound() {
  return (
    <>
      <MatrixRain />
      <div className="min-h-screen flex flex-col items-center justify-center px-4 text-center">
        <GlitchText
          text="404"
          as="h1"
          className="font-pixel text-6xl sm:text-8xl text-neon-pink mb-4"
        />
        <div className="terminal-window max-w-md p-6 mb-8">
          <div className="text-neon-cyan text-lg mb-2">{">"} FILE NOT FOUND</div>
          <p className="text-matrix-green/60 text-sm">
            This page has been redacted. Or maybe it never existed. Just like your senator&apos;s
            integrity.
          </p>
        </div>
        <Link href="/" className="btn-retro">
          [ RETURN TO BASE ]
        </Link>
      </div>
    </>
  );
}
