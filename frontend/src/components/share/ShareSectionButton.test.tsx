import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ShareSectionButton from "./ShareSectionButton";
import { ShareSubjectProvider } from "./ShareSubjectContext";
import ScorecardDrawer from "@/components/scorecard/ScorecardDrawer";
import type { ShareSubject } from "@/lib/shareImage";

const captureSection = vi.fn();
vi.mock("@/lib/shareImage", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/shareImage")>()),
  captureSection: (...args: unknown[]) => captureSection(...args),
}));

const subject: ShareSubject = {
  title: "Tim Burchett",
  subtitle: "Representative · TN-2 · Republican",
  url: "https://civitas-research.org/politicians/tim-burchett",
};

function Section({ children }: { children: React.ReactNode }) {
  return (
    <ShareSubjectProvider subject={subject}>
      <section data-share-section="funding-independence">
        <p>Funding facts</p>
        {children}
      </section>
    </ShareSubjectProvider>
  );
}

const write = vi.fn();

beforeEach(() => {
  captureSection.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
  write.mockResolvedValue(undefined);
  vi.stubGlobal(
    "ClipboardItem",
    class {
      constructor(public items: Record<string, Blob>) {}
    }
  );
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});

/** userEvent.setup() installs its own clipboard stub, so ours goes in after. */
function setup() {
  const user = userEvent.setup();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { write, writeText: vi.fn().mockResolvedValue(undefined) },
  });
  return user;
}

afterEach(() => {
  vi.unstubAllGlobals();
  captureSection.mockReset();
});

describe("ShareSectionButton", () => {
  it("renders nothing without a share subject", () => {
    render(
      <section data-share-section="x">
        <ShareSectionButton label="Funding Independence" />
      </section>
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders nothing where no section encloses it", () => {
    render(
      <ShareSubjectProvider subject={subject}>
        <ShareSectionButton label="Funding Independence" />
      </ShareSubjectProvider>
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("captures its own section and copies the image", async () => {
    const user = setup();
    render(
      <Section>
        <ShareSectionButton label="Funding Independence" />
      </Section>
    );

    await user.click(
      screen.getByRole("button", { name: "Share Funding Independence as an image" })
    );

    const dialog = screen.getByRole("dialog", { name: "Share · Funding Independence" });
    const [section, passedSubject, opts] = captureSection.mock.calls[0];
    expect((section as HTMLElement).dataset.shareSection).toBe("funding-independence");
    expect(passedSubject).toBe(subject);
    expect(opts).toMatchObject({
      link: "https://civitas-research.org/politicians/tim-burchett#funding-independence",
      withStrip: true,
    });

    expect(await screen.findByAltText(/The image to be shared/)).toHaveAttribute(
      "src",
      "blob:preview"
    );
    expect(dialog).toHaveTextContent(
      "https://civitas-research.org/politicians/tim-burchett#funding-independence"
    );

    await user.click(screen.getByRole("button", { name: "Copy image" }));
    expect(write).toHaveBeenCalledTimes(1);
    expect(write.mock.calls[0][0][0].items["image/png"]).toBeInstanceOf(Blob);
    expect(screen.getByRole("status")).toHaveTextContent("Image copied");
  });

  it("links to the page itself when the section has no anchor", async () => {
    const user = setup();
    render(
      <Section>
        <ShareSectionButton label="Senate" anchor={null} />
      </Section>
    );
    await user.click(screen.getByRole("button", { name: "Share Senate as an image" }));
    expect(
      screen.getByText("https://civitas-research.org/politicians/tim-burchett")
    ).toBeInTheDocument();
    expect(captureSection.mock.calls[0][2]).toMatchObject({
      link: "https://civitas-research.org/politicians/tim-burchett",
    });
  });

  it("says so, and keeps the link, when the image can't be made", async () => {
    captureSection.mockRejectedValue(new Error("no canvas"));
    const user = setup();
    render(
      <Section>
        <ShareSectionButton label="Funding Independence" />
      </Section>
    );
    await user.click(screen.getByRole("button", { name: /as an image/ }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(/couldn.t be made in this browser/)
    );
    expect(screen.getByRole("button", { name: "Copy image" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Copy link" })).toBeEnabled();
  });

  // Opened from inside a drawer, Escape used to close the drawer too: both
  // dialogs listened on the document.
  it("closes on Escape without closing the drawer it was opened from", async () => {
    const user = setup();
    const closeDrawer = vi.fn();
    render(
      <ScorecardDrawer title="Donors" subtitle="Tim Burchett" onClose={closeDrawer}>
        <Section>
          <ShareSectionButton label="Funding Independence" />
        </Section>
      </ScorecardDrawer>
    );
    await user.click(screen.getByRole("button", { name: /as an image/ }));
    expect(screen.getByRole("dialog", { name: /Share/ })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog", { name: /Share/ })).toBeNull());
    expect(closeDrawer).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /as an image/ })).toHaveFocus();
  });
});
