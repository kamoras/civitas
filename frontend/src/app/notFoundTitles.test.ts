import { describe, expect, it } from "vitest";
import { metadata as rootNotFound } from "./not-found";
import { metadata as exploreDoc } from "./explore/[id]/layout";
import { metadata as day } from "./congress/[date]/layout";
import { metadata as week } from "./congress/week/[date]/layout";
import { metadata as month } from "./congress/month/[month]/layout";
import { metadata as withThePresident } from "./congress/president/[chamber]/layout";

// A route's notFound() discards its page's metadata: a 404's title comes
// from the layouts above it, or the root not-found for an address no route
// answers. Without these, all six read only the site's name.
describe("404 titles", () => {
  it.each([
    ["an unknown address", rootNotFound, "Page not found"],
    ["an Explore document", exploreDoc, "Document not found"],
    ["a Congress day", day, "Congress day not found"],
    ["a Congress week", week, "Congress week not found"],
    ["a Congress month", month, "Congress month not found"],
    ["votes with the president", withThePresident, "Votes with the president: not found"],
  ])("%s", (_, metadata, title) => {
    expect(metadata.title).toBe(title);
  });
});
