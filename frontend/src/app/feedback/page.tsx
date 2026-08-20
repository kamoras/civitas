"use client";

import { useState } from "react";
import Navbar from "@/components/layout/Navbar";
import TerminalTitlebar from "@/components/TerminalTitlebar";
import Footer from "@/components/layout/Footer";
import PageMasthead from "@/components/layout/PageMasthead";
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
  const [status, setStatus] = useState<Status>("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [issueUrl, setIssueUrl] = useState<string | null>(null);

  /** The page the reporter came from, read at submit time rather than stored.
   *  It was captured into state by a mount effect purely so it could be read
   *  once, in the handler below — an extra render on every visit to hold a
   *  value nothing renders. Same-origin check unchanged: an off-site referrer
   *  is not ours to forward. */
  function referringPage(): string | undefined {
    const ref = document.referrer;
    return ref && ref.includes(window.location.hostname) ? ref : undefined;
  }

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
        pageUrl: referringPage(),
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
      <main id="main-content" tabIndex={-1} className="pt-[var(--header-clearance)] pb-16 px-4">
        <div className="max-w-2xl mx-auto">
          <PageMasthead
            className="mb-8"
            eyebrow="Feedback · bugs, ideas, access barriers"
            title="Feedback"
          >
            <p>Report a bug, suggest an idea, or flag an accessibility barrier.</p>
          </PageMasthead>

          <TerminalTitlebar title="Feedback" />
          <div className="border border-t-0 border-white/[0.07] bg-surface-base p-6">
            {status === "success" ? (
              <div className="text-center py-8 space-y-4">
                <p className="font-mono text-base text-ink-hi">
                  Thanks — your feedback has been received.
                </p>
                {issueUrl && (
                  <p className="font-mono text-xs text-ink-min">
                    Filed as a public issue:{" "}
                    <a
                      href={issueUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-signal-cyan hover:underline"
                    >
                      #{issueUrl.split("/").pop()}
                    </a>
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setStatus("idle");
                    setMessage("");
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
                  <p className="font-mono text-xs mt-1 flex items-center justify-between">
                    {/* The button disables below MESSAGE_MIN with nothing on
                        the page saying why — reported live as "blocked from
                        submitting" with no visible cause. This is the cause,
                        made visible instead of silent. */}
                    <span className="text-signal-amber">
                      {trimmed.length > 0 && trimmed.length < MESSAGE_MIN
                        ? `${MESSAGE_MIN - trimmed.length} more character${
                            MESSAGE_MIN - trimmed.length === 1 ? "" : "s"
                          } needed`
                        : ""}
                    </span>
                    <span className="text-ink-min">
                      {trimmed.length} / {MESSAGE_MAX}
                    </span>
                  </p>
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
                  Filed as a public issue in our{" "}
                  <a
                    href="https://github.com/kamoras/civitas"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-ink-lo hover:underline"
                  >
                    open-source repo
                  </a>
                  . Don&apos;t include anything you wouldn&apos;t want public — including your
                  own email, if you&apos;d rather not.
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
