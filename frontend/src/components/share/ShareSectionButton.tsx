"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Modal from "@/components/shared/Modal";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { BOXED_CONTROL } from "@/lib/controlStyles";
import {
  SHARE_EXCLUDE_ATTR,
  SHARE_SECTION_ATTR,
  canCopyImage,
  canShareImage,
  captureSection,
  sectionUrl,
  shareFileName,
} from "@/lib/shareImage";
import { useShareSubject } from "./ShareSubjectContext";

type Capture =
  { state: "working" } | { state: "ready"; blob: Blob; previewUrl: string } | { state: "failed" };

const ACTION =
  "inline-flex min-h-[44px] items-center border px-3 font-mono text-xs uppercase tracking-[0.12em] transition-colors";

/**
 * "Share" for one section of a page: turns the section (the nearest
 * ancestor carrying `data-share-section`) into an image, shows it, and
 * offers to copy it, save it, or hand it to the system share sheet, plus
 * the section's link.
 *
 * The button itself, and anything else marked `data-share-exclude`, is left
 * out of the picture. Renders nothing outside a `ShareSubjectProvider`, or
 * where no section encloses it.
 */
export default function ShareSectionButton({
  label,
  withStrip = true,
  anchor,
  children = "Share",
  className = `inline-flex min-h-6 items-center border px-2 font-mono text-xs uppercase tracking-[0.12em] transition-colors ${BOXED_CONTROL.unselected}`,
}: {
  /** The section's name, for the button's accessible name and the dialog. */
  label: string;
  /** False when the section already names the page's subject itself. */
  withStrip?: boolean;
  /** The fragment (no "#") the section's link ends in. Defaults to the
   *  section's own id; null when the page has nothing to scroll to for it,
   *  so the link is the page itself. */
  anchor?: string | null;
  /** The button's visible text. */
  children?: string;
  /** Replaces the button's default classes. */
  className?: string;
}) {
  const subject = useShareSubject();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [capture, setCapture] = useState<Capture>({ state: "working" });
  const [sectionId, setSectionId] = useState("");
  const [status, setStatus] = useState("");
  const [linkCopied, copyLink] = useCopyFeedback(1500);
  const [abilities, setAbilities] = useState({ copy: false, share: false });
  // Bumped on every open, so a capture that finishes after its dialog was
  // closed (or reopened) is dropped rather than shown.
  const generation = useRef(0);
  const [hasSection, setHasSection] = useState(true);

  useEffect(() => {
    setAbilities({ copy: canCopyImage(), share: canShareImage() });
    setHasSection(!!buttonRef.current?.closest(`[${SHARE_SECTION_ATTR}]`));
  }, []);

  useEffect(() => {
    if (capture.state !== "ready") return;
    return () => URL.revokeObjectURL(capture.previewUrl);
  }, [capture]);

  const close = useCallback(() => {
    generation.current += 1;
    setOpen(false);
    setCapture({ state: "working" });
    setStatus("");
  }, []);

  if (!subject || !hasSection) return null;

  /** "Tim Burchett — Funding Independence"; just the title when the label
   *  is the title (an Action Center card is labelled by its issue). */
  function describe(): string {
    if (!subject) return label;
    return label === subject.title ? label : `${subject.title} — ${label}`;
  }

  function linkFor(id: string): string {
    if (!subject || anchor === null) return subject?.url ?? "";
    return sectionUrl(subject.url, anchor ?? id);
  }

  function start() {
    const section = buttonRef.current?.closest<HTMLElement>(`[${SHARE_SECTION_ATTR}]`);
    if (!section || !subject) return;
    const id = section.getAttribute(SHARE_SECTION_ATTR) || "section";
    const mine = ++generation.current;
    setSectionId(id);
    setCapture({ state: "working" });
    setStatus("");
    setOpen(true);
    // Set once the dialog (and its live region) is in the page: text a live
    // region is inserted with usually isn't announced. Skipped if the capture
    // already finished, or this open was closed or replaced by another.
    setTimeout(() => {
      if (generation.current === mine) setStatus((s) => (s === "" ? "Making the image…" : s));
    }, 100);
    captureSection(section, subject, { link: linkFor(id), withStrip })
      .then((blob) => {
        if (generation.current !== mine) return;
        setCapture({ state: "ready", blob, previewUrl: URL.createObjectURL(blob) });
        setStatus("Image ready.");
      })
      .catch(() => {
        if (generation.current !== mine) return;
        setCapture({ state: "failed" });
        setStatus("The image couldn't be made in this browser. The link still works.");
      });
  }

  const link = linkFor(sectionId || "section");
  const fileName = shareFileName(subject.url, sectionId || "section");

  async function copyImage() {
    if (capture.state !== "ready") return;
    try {
      await navigator.clipboard.write([new ClipboardItem({ "image/png": capture.blob })]);
      setStatus("Image copied. Paste it into a message or post.");
    } catch {
      setStatus("This browser wouldn't copy the image. Use Save image instead.");
    }
  }

  async function shareImage() {
    if (capture.state !== "ready" || !subject) return;
    const file = new File([capture.blob], fileName, { type: "image/png" });
    try {
      await navigator.share({ files: [file], title: describe(), text: link });
    } catch (e) {
      // Dismissing the sheet rejects with AbortError; that is not a failure.
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setStatus("Sharing didn't work here. Use Save image instead.");
      }
    }
  }

  function saveImage() {
    if (capture.state !== "ready") return;
    const a = document.createElement("a");
    a.href = capture.previewUrl;
    a.download = fileName;
    a.click();
    // Only a request: some browsers (iOS Safari) open the image instead of
    // saving it, so this can't claim the file was saved.
    setStatus(`Downloading ${fileName}.`);
  }

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={start}
        {...{ [SHARE_EXCLUDE_ATTR]: "" }}
        aria-label={`Share ${label} as an image`}
        aria-haspopup="dialog"
        className={className}
      >
        {children}
      </button>

      <Modal open={open} onClose={close} title={`Share · ${label}`}>
        <div className="flex flex-col gap-4">
          <div className="flex min-h-40 items-center justify-center border border-white/[0.07] bg-surface">
            {capture.state === "working" && (
              // Announced through the status line below.
              <p aria-hidden="true" className="font-mono text-xs text-ink-min">
                Making the image…
              </p>
            )}
            {capture.state === "failed" && (
              // Announced through the status line below; hidden here so a
              // screen reader doesn't read it twice.
              <p aria-hidden="true" className="px-4 py-6 font-mono text-xs text-ink-lo">
                The image couldn&apos;t be made in this browser. The link below still works.
              </p>
            )}
            {capture.state === "ready" && (
              // eslint-disable-next-line @next/next/no-img-element -- a local blob: preview
              <img
                src={capture.previewUrl}
                alt={`The image to be shared: ${describe()}`}
                className="h-auto max-h-[50vh] w-full object-contain"
              />
            )}
          </div>

          <div className="flex flex-wrap gap-2">
            {abilities.share && (
              <button
                type="button"
                onClick={shareImage}
                disabled={capture.state !== "ready"}
                className={`${ACTION} ${BOXED_CONTROL.unselected} disabled:opacity-50`}
              >
                Share…
              </button>
            )}
            {abilities.copy && (
              <button
                type="button"
                onClick={copyImage}
                disabled={capture.state !== "ready"}
                className={`${ACTION} ${BOXED_CONTROL.unselected} disabled:opacity-50`}
              >
                Copy image
              </button>
            )}
            <button
              type="button"
              onClick={saveImage}
              disabled={capture.state !== "ready"}
              className={`${ACTION} ${BOXED_CONTROL.unselected} disabled:opacity-50`}
            >
              Save image
            </button>
            <button
              type="button"
              onClick={() =>
                copyLink(link).then((ok) => {
                  // Cleared first, so a second copy is announced again
                  // rather than being the same text the live region has.
                  setStatus("");
                  setTimeout(
                    () => setStatus(ok ? "Link copied." : "This browser wouldn't copy the link."),
                    100
                  );
                })
              }
              className={`${ACTION} ${linkCopied ? BOXED_CONTROL.selected : BOXED_CONTROL.unselected}`}
            >
              {linkCopied ? "Link copied" : "Copy link"}
            </button>
          </div>

          <p className="break-all font-mono text-xs text-ink-min">{link}</p>
          <p role="status" aria-live="polite" className="min-h-4 font-mono text-xs text-ink-lo">
            {status}
          </p>
        </div>
      </Modal>
    </>
  );
}
