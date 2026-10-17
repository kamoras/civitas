import { afterEach, describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import VoteMembers from "./VoteMembers";
import { YOUR_STATE_KEY } from "@/hooks/useYourState";
import type { VoteMember } from "@/types/congress";

const m = (lastName: string, state: string, bucket: VoteMember["bucket"]): VoteMember => ({
  lastName,
  firstName: "",
  party: "R",
  state,
  position: bucket,
  bucket,
  page: `/politicians/${lastName.toLowerCase()}`,
});
const members = [m("Alpha", "OH", "yea"), m("Bravo", "OH", "nay"), m("Charlie", "TX", "yea")];

afterEach(() => sessionStorage.clear());

describe("VoteMembers: the reader's state first", () => {
  it("lists the header state's members and their votes before any list is opened", () => {
    // Mobile spec P4/P9: a roll call's 100 names, the reader's two among them.
    sessionStorage.setItem(YOUR_STATE_KEY, "OH");
    render(<VoteMembers members={members} collapsible />);
    const mine = screen.getByRole("region", { name: "Your state: Ohio" });
    const rows = within(mine).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual(["AlphaRYea", "BravoRNay"]);
    expect(within(mine).getByRole("link", { name: "Alpha" })).toHaveAttribute(
      "href",
      "/politicians/alpha"
    );
    for (const r of rows) expect(r.className).toContain("min-h-11");
    // Still collapsed: the rest waits behind "Show all".
    expect(screen.getByRole("button", { name: "Show all 3 members' votes" })).toBeTruthy();
  });

  it("adds nothing with no state set, or a state with no member on the vote", () => {
    const { unmount } = render(<VoteMembers members={members} />);
    expect(screen.queryByRole("region", { name: /^Your state/ })).toBeNull();
    unmount();
    sessionStorage.setItem(YOUR_STATE_KEY, "CA");
    render(<VoteMembers members={members} />);
    expect(screen.queryByRole("region", { name: /^Your state/ })).toBeNull();
  });
});
