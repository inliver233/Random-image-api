import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { QueryState } from "./QueryState";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function PartialHarness() {
  const ok = useQuery({
    queryKey: ["partial-ok"],
    queryFn: async () => ({ ok: true, tag: "alive" }),
  });
  const bad = useQuery({
    queryKey: ["partial-bad"],
    queryFn: async () => {
      throw new Error("boom");
    },
  });
  return (
    <QueryState
      partial
      queries={[ok, bad]}
      errorMessages={["加载 OK 失败", "加载 BAD 失败"]}
    >
      <div data-testid="partial-body">
        {ok.data ? <span>alive-tag</span> : null}
        {bad.data ? <span>should-not-show</span> : null}
      </div>
    </QueryState>
  );
}

describe("QueryState partial", () => {
  it("keeps sibling content when one query errors", async () => {
    const qc = makeClient();
    // silence expected query error logs
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <QueryClientProvider client={qc}>
        <PartialHarness />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("alive-tag")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("加载 BAD 失败")).toBeInTheDocument();
    });
    expect(screen.queryByText("should-not-show")).not.toBeInTheDocument();
  });
});
