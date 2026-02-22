import { beforeEach, describe, expect, it } from "vitest";

import { clearAdminToken, getAdminToken, setAdminToken } from "./tokenStorage";

describe("tokenStorage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("stores and clears admin token", () => {
    expect(getAdminToken()).toBeNull();
    setAdminToken("t1");
    expect(getAdminToken()).toBe("t1");
    clearAdminToken();
    expect(getAdminToken()).toBeNull();
  });
});

