import { beforeEach, describe, expect, it, vi } from "vitest";

const permanentRedirect = vi.hoisted(() =>
  vi.fn((url: string) => {
    throw new Error(`PERMANENT_REDIRECT:${url}`);
  })
);
const notFound = vi.hoisted(() =>
  vi.fn(() => {
    throw new Error("NOT_FOUND");
  })
);
vi.mock("next/navigation", () => ({ permanentRedirect, notFound }));
vi.mock("./PoliticianProfileClient", () => ({ default: () => null }));

import PoliticianProfilePage, { generateMetadata } from "./page";

const PROFILE = {
  id: "ana-nunez",
  branch: "house",
  identity: { name: "Ana María Núñez", party: "D", state: "NM", role: "Representative" },
  hasScorecard: false,
  scorecard: null,
  activeIssues: [],
  governmentRecord: { recent: [] },
};

function backendAnswers(body: unknown, status = 200) {
  (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
    ok: status < 400,
    status,
    json: async () => body,
  });
}

const params = (id: string) => ({ params: Promise.resolve({ id }) });

// A member id renamed since a URL was posted or indexed: the backend answers
// the old id with the member under the current one, and the page moves the
// reader (and search engines) there for good.
describe("/politicians/[id]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("permanently redirects a renamed member's old id to the current one", async () => {
    backendAnswers(PROFILE);
    await expect(PoliticianProfilePage(params("a-nez"))).rejects.toThrow(
      "PERMANENT_REDIRECT:/politicians/ana-nunez"
    );
    expect(notFound).not.toHaveBeenCalled();
  });

  it("renders the current id without redirecting", async () => {
    backendAnswers(PROFILE);
    await expect(PoliticianProfilePage(params("ana-nunez"))).resolves.toBeTruthy();
    expect(permanentRedirect).not.toHaveBeenCalled();
  });

  it("gives an old id the current id's canonical, never its own", async () => {
    backendAnswers(PROFILE);
    const meta = await generateMetadata(params("a-nez"));
    expect(String(meta.alternates?.canonical)).toContain("/politicians/ana-nunez");
    expect(String(meta.alternates?.canonical)).not.toContain("a-nez");
  });

  it("404s an id nobody has held", async () => {
    backendAnswers({ detail: "Politician not found" }, 404);
    await expect(PoliticianProfilePage(params("nobody"))).rejects.toThrow("NOT_FOUND");
    expect(permanentRedirect).not.toHaveBeenCalled();
  });
});
