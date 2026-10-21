/**
 * The share card of each page that is a section rather than a record: its
 * own eyebrow, title and one line, drawn by `/api/og?page=<path>` on the
 * section card template. `pageMetadata` (lib/site.ts) gives a page listed
 * here this card instead of the site card, so a shared link says which page
 * it is.
 *
 * Keyed by path so the card route only ever draws text from this list: a
 * card taking its words from the query string would let anyone render any
 * sentence under the Civitas name. Record pages (a member, a bill, a state's
 * ballot) have cards drawn from their records instead, in app/api/og.
 */
export type SectionCard = { eyebrow: string; title: string; subtitle: string };

export const SECTION_CARDS: Record<string, SectionCard> = {
  "/about": {
    eyebrow: "About",
    title: "How Civitas compares members of Congress",
    subtitle: "Where every number comes from, how each one is worked out, and what it can't say.",
  },
  "/about/scores": {
    eyebrow: "About",
    title: "How senators and representatives are compared",
    subtitle: "Money, votes and bills, each beside the members who hold the same kind of seat.",
  },
  "/about/presidents-and-justices": {
    eyebrow: "About",
    title: "Presidents and justices",
    subtitle: "What the record shows for each presidency, and why justices are not scored.",
  },
  "/about/elections": {
    eyebrow: "About",
    title: "How state ballot pages work",
    subtitle: "Candidates and ballot measures, quoted from each state's own sources.",
  },
  "/about/news": {
    eyebrow: "About",
    title: "How the Action Center and Congress reports work",
    subtitle: "Stories in their sources' own words, and the day's record from Congress.",
  },
  "/about/data": {
    eyebrow: "About",
    title: "Data sources, AI use and privacy",
    subtitle: "Every source, what software does with it, and what the site never collects.",
  },
  "/about/limitations": {
    eyebrow: "About",
    title: "Known limitations",
    subtitle: "What the data can't tell you, stated plainly.",
  },
  "/about/references": {
    eyebrow: "About",
    title: "References",
    subtitle: "The research behind each method on the site.",
  },
  "/accessibility": {
    eyebrow: "Accessibility",
    title: "Accessibility statement",
    subtitle: "The standard the site is built to, and how to report a barrier.",
  },
  "/action": {
    eyebrow: "Action Center",
    title: "Today's civic issues and what you can do",
    subtitle: "Each story in its sources' own words, with the bills, members and comment periods.",
  },
  "/agencies": {
    eyebrow: "Agencies",
    title: "Federal agencies",
    subtitle: "What each agency spends, the rules it proposes and when you can comment.",
  },
  "/changelog": {
    eyebrow: "Changelog",
    title: "What changed on Civitas",
    subtitle: "What is new, and every change to how members are measured, with why.",
  },
  "/compare": {
    eyebrow: "Congress",
    title: "Compare members of Congress side by side",
    subtitle: "Small gifts, PAC money, party breaks and bills moved, for any two members.",
  },
  "/compare/presidents": {
    eyebrow: "Executive",
    title: "Compare presidents side by side",
    subtitle: "Approval, the economy, executive orders and the historians' rank.",
  },
  "/congress": {
    eyebrow: "Congress",
    title: "What happened on the floor",
    subtitle: "The Senate's and the House's day, from the official record.",
  },
  "/congress/bills": {
    eyebrow: "Congress",
    title: "Bills in Congress",
    subtitle: "Where each bill stands, who sponsors it and what is moving now.",
  },
  "/congress/committees": {
    eyebrow: "Congress",
    title: "Committee hearings, markups and reports",
    subtitle: "What each committee is doing, from the chambers' own schedules.",
  },
  "/congress/members": {
    eyebrow: "Congress",
    title: "Find your members of Congress",
    subtitle: "Your two senators and your representative, by state, town or county.",
  },
  "/congress/members/measures": {
    eyebrow: "Congress",
    title: "Congress by the numbers",
    subtitle: "Small gifts, PAC money, party breaks and bills, side by side, with no rank.",
  },
  "/congress/nominations": {
    eyebrow: "Congress",
    title: "Nominations and confirmations",
    subtitle: "Every presidential nomination before the Senate, and how long each took.",
  },
  "/congress/president/senate": {
    eyebrow: "Congress",
    title: "Senators and the president's positions",
    subtitle: "How often each senator voted the way the White House said it wanted.",
  },
  "/congress/president/house": {
    eyebrow: "Congress",
    title: "Representatives and the president's positions",
    subtitle: "How often each representative voted the way the White House said it wanted.",
  },
  "/court": {
    eyebrow: "Supreme Court",
    title: "The Supreme Court",
    subtitle: "The justices, their votes and the Court's decisions, from public case records.",
  },
  "/developers": {
    eyebrow: "Developers",
    title: "Public API and MCP server",
    subtitle: "The same records the site shows, free to query, documented from the code.",
  },
  "/elections": {
    eyebrow: "Elections",
    title: "Elections",
    subtitle:
      "Every state's ballot: federal contests and statewide measures, from official sources.",
  },
  "/elections/archive": {
    eyebrow: "Elections",
    title: "Past general elections",
    subtitle: "Each election's count as its states reported it, and the ballot it was on.",
  },
  "/elections/calendar": {
    eyebrow: "Elections",
    title: "Election calendar",
    subtitle: "Primaries, runoffs, special elections and each state's voter deadlines.",
  },
  "/elections/map": {
    eyebrow: "Elections",
    title: "Senate and House map",
    subtitle: "How each seat usually votes, and a range for the margin. Not a race call.",
  },
  "/elections/redistricting": {
    eyebrow: "Elections",
    title: "New congressional maps",
    subtitle: "The states that redrew their districts, and what changed in each.",
  },
  "/environmental": {
    eyebrow: "Environmental impact",
    title: "What running Civitas draws",
    subtitle: "One Raspberry Pi 5: its power, yearly energy and carbon, measured.",
  },
  "/executive": {
    eyebrow: "Executive",
    title: "The executive branch",
    subtitle: "The president's orders, federal rules open for comment, agencies and nominations.",
  },
  "/executive/presidents": {
    eyebrow: "Executive",
    title: "Every U.S. president",
    subtitle: "Party, years in office and the C-SPAN historians' rank of each presidency.",
  },
  "/explore": {
    eyebrow: "Explore",
    title: "Search the federal record",
    subtitle: "Congressional records, floor speeches, rules and executive orders.",
  },
  "/feedback": {
    eyebrow: "Feedback",
    title: "Tell us what's wrong",
    subtitle: "Report a data error or a barrier, or suggest a feature.",
  },
  "/feeds": {
    eyebrow: "Feeds",
    title: "Follow Civitas without an account",
    subtitle: "Atom feeds by topic, state, member, bill and agency.",
  },
  "/search": {
    eyebrow: "Search",
    title: "Search Civitas",
    subtitle: "Members, bills, agencies and documents in one place.",
  },
};

/** The card's description for `og:image:alt`: what it shows, in its words. */
export function sectionCardAlt(card: SectionCard): string {
  return `${card.eyebrow}: ${card.title}. ${card.subtitle}`;
}
