"use client";

import { useEffect, useState } from "react";
import Navbar from "@/components/layout/Navbar";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import MatrixRain from "@/components/effects/MatrixRain";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
import GlitchText from "@/components/effects/GlitchText";
import { submitFeedback, type FeedbackSubmission } from "@/lib/api";

const CATEGORIES: { value: FeedbackSubmission["category"]; label: string }[] = [
  { value: "bug", label: "Something's broken" },
  { value: "idea", label: "Feature idea" },
  { value: "accessibility", label: "Accessibility barrier" },
  { value: "data", label: "Data question or correction" },
  { value: "other", label: "Other" },
];

const MESSAGE_MIN = 10;
const MESSAGE_MAX = 4000;

type Status = "idle" | "submitting" | "success" | "error";

export default function FeedbackPage() {
  const [category, setCategory] = useState<FeedbackSubmission["category"]>("bug");
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [pageUrl, setPageUrl] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [issueUrl, setIssueUrl] = useState<string | null>(null);

  useEffect(() => {
    if (document.referrer && document.referrer.includes(window.location.hostname)) {
      setPageUrl(document.referrer);
    }
  }, []);

  const trimmed = message.trim();
  const canSubmit = trimmed.length >= MESSAGE_MIN && trimmed.length <= MESSAGE_MAX && status !== "submitting";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setStatus("submitting");
    setErrorMessage("");
    try {
      const res = await submitFeedback({ category, message: trimmed, email: email.trim() || undefined, pageUrl: pageUrl || undefined });
      setIssueUrl(res.issueUrl);
      setStatus("success");
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      setStatus("error");
    }
  }

  return (
    <div className="min-h-screen bg-crt-black text-matrix-green">
      <MatrixRain />
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-2xl mx-auto">

          <div className="text-center mb-8">
            <GlitchText
              as="h1"
              text="FEEDBACK"
              className="font-pixel text-xl sm:text-3xl text-matrix-green neon-green mb-2 block"
            />
            <p className="font-mono text-xs text-matrix-green/40">
              REPORT A BUG, SUGGEST AN IDEA, OR FLAG AN ACCESSIBILITY BARRIER
            </p>
          </div>

          <TerminalTitlebar title="feedback.dat" />
          <div className="border border-t-0 border-matrix-green/20 bg-crt-black/40 p-6">

            {status === "success" ? (
              <div className="text-center py-8 space-y-4">
                <p className="font-mono text-sm text-matrix-green">
                  Thanks — your feedback has been received.
                </p>
                {issueUrl && (
                  <p className="font-mono text-xs text-matrix-green/40">
                    Tracked internally as{" "}
                    <span className="text-matrix-green/60">{issueUrl.split("/").pop()}</span>.
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setStatus("idle");
                    setMessage("");
                    setEmail("");
                    setIssueUrl(null);
                  }}
                  className="font-mono text-[10px] text-neon-cyan hover:underline tracking-widest"
                >
                  SUBMIT MORE FEEDBACK
                </button>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-5">
                <div>
                  <label htmlFor="category" className="block font-mono text-[10px] uppercase tracking-widest text-matrix-green/50 mb-1.5">
                    Category
                  </label>
                  <select
                    id="category"
                    value={category}
                    onChange={(e) => setCategory(e.target.value as FeedbackSubmission["category"])}
                    className="w-full font-mono text-sm bg-crt-black border border-matrix-green/20 focus:border-matrix-green/60 text-matrix-green px-3 py-2 outline-none"
                  >
                    {CATEGORIES.map((c) => (
                      <option key={c.value} value={c.value}>{c.label}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label htmlFor="message" className="block font-mono text-[10px] uppercase tracking-widest text-matrix-green/50 mb-1.5">
                    What&apos;s on your mind?
                  </label>
                  <textarea
                    id="message"
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    required
                    minLength={MESSAGE_MIN}
                    maxLength={MESSAGE_MAX}
                    rows={6}
                    placeholder="Describe what you were trying to do, what happened, and what you expected instead."
                    className="w-full font-mono text-sm bg-crt-black border border-matrix-green/20 focus:border-matrix-green/60 text-matrix-green placeholder-matrix-green/25 px-3 py-2 outline-none resize-y"
                  />
                  <p className="font-mono text-[10px] text-matrix-green/30 mt-1 text-right">
                    {trimmed.length} / {MESSAGE_MAX}
                  </p>
                </div>

                <div>
                  <label htmlFor="email" className="block font-mono text-[10px] uppercase tracking-widest text-matrix-green/50 mb-1.5">
                    Email <span className="text-matrix-green/30 normal-case">(optional — only if you want a reply)</span>
                  </label>
                  <input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    className="w-full font-mono text-sm bg-crt-black border border-matrix-green/20 focus:border-matrix-green/60 text-matrix-green placeholder-matrix-green/25 px-3 py-2 outline-none"
                  />
                </div>

                {status === "error" && (
                  <p className="font-mono text-xs text-red-400/80">{errorMessage}</p>
                )}

                <button
                  type="submit"
                  disabled={!canSubmit}
                  className="w-full font-mono text-xs tracking-widest px-4 py-2.5 border border-neon-cyan/40 text-neon-cyan hover:bg-neon-cyan/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors uppercase"
                >
                  {status === "submitting" ? "SUBMITTING..." : "SUBMIT FEEDBACK"}
                </button>

                <p className="font-mono text-[10px] text-matrix-green/30 text-center">
                  Feedback is tracked internally and not published publicly.
                </p>
              </form>
            )}
          </div>

        </div>
      </main>
      <BackToTop />
      <Footer />
    </div>
  );
}
