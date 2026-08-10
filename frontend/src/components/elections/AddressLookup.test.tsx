import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AddressLookup from "./AddressLookup";
import { fetchDistrictForAddress } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchDistrictForAddress: vi.fn(),
}));

const mockFetch = vi.mocked(fetchDistrictForAddress);

async function submit(user: ReturnType<typeof userEvent.setup>, address = "123 Main St") {
  await user.type(screen.getByLabelText("Your address"), address);
  await user.click(screen.getByRole("button", { name: /find my district/i }));
}

describe("AddressLookup", () => {
  it("calls onResolved with the district on a same-state match", async () => {
    mockFetch.mockResolvedValue({ state: "GA", district: 6 });
    const onResolved = vi.fn().mockReturnValue(true);
    const user = userEvent.setup();
    render(<AddressLookup ballotState="GA" onResolved={onResolved} />);

    await submit(user);

    await waitFor(() => expect(onResolved).toHaveBeenCalledWith(6));
    expect(screen.queryByText(/not in our House data/i)).not.toBeInTheDocument();
  });

  it("shows a district-not-found message when onResolved can't find that district", async () => {
    // Real gap: Census's district vintage can disagree with Civitas's own
    // race data, so a resolved district isn't guaranteed to exist here.
    // Silently doing nothing would look like the lookup just failed.
    mockFetch.mockResolvedValue({ state: "GA", district: 6 });
    const onResolved = vi.fn().mockReturnValue(false);
    const user = userEvent.setup();
    render(<AddressLookup ballotState="GA" onResolved={onResolved} />);

    await submit(user);

    expect(await screen.findByText(/not in our House data for GA/i)).toBeInTheDocument();
  });

  it("points the visitor at the other state when the address resolves elsewhere", async () => {
    mockFetch.mockResolvedValue({ state: "AZ", district: 3 });
    const user = userEvent.setup();
    render(<AddressLookup ballotState="GA" onResolved={vi.fn()} />);

    await submit(user);

    expect(await screen.findByText(/That address is in AZ, not GA/i)).toBeInTheDocument();
  });

  it("shows a no-match message when Census can't resolve the address", async () => {
    mockFetch.mockResolvedValue({ state: null, district: null });
    const user = userEvent.setup();
    render(<AddressLookup ballotState="GA" onResolved={vi.fn()} />);

    await submit(user);

    expect(await screen.findByText(/Couldn't match that address/i)).toBeInTheDocument();
  });

  it("shows an error message when the lookup request fails (e.g. rate limited)", async () => {
    mockFetch.mockRejectedValue(new Error("Failed to resolve address: 429"));
    const user = userEvent.setup();
    render(<AddressLookup ballotState="GA" onResolved={vi.fn()} />);

    await submit(user);

    expect(
      await screen.findByText(/Could not resolve that address right now/i)
    ).toBeInTheDocument();
  });
});
