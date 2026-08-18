"use client";

import { useEffect, useState } from "react";
import Navbar from "@/components/layout/Navbar";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import Footer from "@/components/layout/Footer";
import BackToTop from "@/components/BackToTop";
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
  const canSubmit =
    trimmed.length >= MESSAGE_MIN && trimmed.length <= MESSAGE_MAX && status !== "submitting";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setStatus("submitting");
    setErrorMessage("");
    try {
      const res = await submitFeedback({
        category,
        message: trimmed,
        email: email.trim() || undefined,
        pageUrl: pageUrl || undefined,
      });
      setIssueUrl(res.issueUrl);
      setStatus("success");
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : "Something went wrong. Please try again."
      );
      setStatus("error");
    }
  }

  return (
    <div className="min-h-screen bg-surface-base text-ink-hi">
      <Navbar />
      <main id="main-content" tabIndex={-1} className="pt-24 pb-16 px-4">
        <div className="max-w-2xl mx-auto">
          <header className="mb-8 border-b-3 border-phos pb-5">
            <p className="font-mono text-xs uppercase tracking-[0.16em] text-ink-min">
              Feedback · bugs, ideas, access barriers
            </p>
            <h1 className="mt-3 font-display text-3xl font-extrabold uppercase leading-none tracking-[-0.02em] text-ink-hi sm:text-4xl">
              Feedback
            </h1>
            <p className="mt-3 max-w-2xl font-display text-base leading-relaxed text-ink-lo">
              Report a bug, suggest an idea, or flag an accessibility barrier.
            </p>
          </header>

          <TerminalTitlebar title="Feedback" />
          <div className="border border-t-0 border-white/[0.07] bg-surface-base p-6">
            {status === "success" ? (
              <div className="text-center py-8 space-y-4">
                <p className="font-mono text-base text-ink-hi">
                  Thanks — your feedback has been received.
                </p>
                {issueUrl && (
                  <p className="font-mono text-xs text-ink-min">
                    Tracked internally as{" "}
                    <span className="text-ink-lo">{issueUrl.split("/").pop()}</span>.
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
                  className="font-mono text-xs text-signal-cyan hover:underline tracking-widest"
                >
                  SUBMIT MORE FEEDBACK
                </button>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-5">
                <div>
                  <label
                    htmlFor="category"
                    className="block font-mono text-xs uppercase tracking-widest text-ink-lo mb-1.5"
                  >
                    Category
                  </label>
                  <select
                    id="category"
                    value={category}
                    onChange={(e) => setCategory(e.target.value as FeedbackSubmission["category"])}
                    className="w-full font-mono text-sm bg-surface-base border border-white/[0.07] focus:border-phos/40 text-ink-hi px-3 py-2 outline-none"
                  >
                    {CATEGORIES.map((c) => (
                      <option key={c.value} value={c.value}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label
                    htmlFor="message"
                    className="block font-mono text-xs uppercase tracking-widest text-ink-lo mb-1.5"
                  >
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
                    className="w-full font-mono text-sm bg-surface-base border border-white/[0.07] focus:border-phos/40 text-ink-hi placeholder-white/15 px-3 py-2 outline-none resize-y"
                  />
                  <p className="font-mono text-xs text-ink-min mt-1 text-right">
                    {trimmed.length} / {MESSAGE_MAX}
                  </p>
                </div>

                <div>
                  <label
                    htmlFor="email"
                    className="block font-mono text-xs uppercase tracking-widest text-ink-lo mb-1.5"
                  >
                    Email{" "}
                    <span className="text-ink-min normal-case">
                      (optional — only if you want a reply)
                    </span>
                  </label>
                  <input
                    id="email"
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    className="w-full font-mono text-sm bg-surface-base border border-white/[0.07] focus:border-phos/40 text-ink-hi placeholder-white/15 px-3 py-2 outline-none"
                  />
                </div>

                {status === "error" && (
                  <p className="font-mono text-xs text-signal-red">{errorMessage}</p>
                )}

                <button
                  type="submit"
                  disabled={!canSubmit}
                  className="w-full font-mono text-xs tracking-widest px-4 py-2.5 border border-signal-cyan/40 text-signal-cyan hover:bg-signal-cyan/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors uppercase"
                >
                  {status === "submitting" ? "SUBMITTING..." : "SUBMIT FEEDBACK"}
                </button>

                <p className="font-mono text-xs text-ink-min text-center">
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
