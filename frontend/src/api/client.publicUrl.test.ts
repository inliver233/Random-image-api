import { describe, expect, it } from "vitest";

import { joinApiBaseUrl } from "./client";

describe("joinApiBaseUrl", () => {
  it("returns relative path when base empty", () => {
    expect(joinApiBaseUrl("", "/random?format=json")).toBe("/random?format=json");
    expect(joinApiBaseUrl("  ", "random")).toBe("/random");
  });

  it("prefixes API origin for split FE/API deploys", () => {
    expect(joinApiBaseUrl("https://api.example.com", "/random?format=json")).toBe(
      "https://api.example.com/random?format=json",
    );
    expect(joinApiBaseUrl("https://api.example.com/", "/random")).toBe("https://api.example.com/random");
  });

  it("leaves absolute URLs unchanged", () => {
    expect(joinApiBaseUrl("https://api.example.com", "https://other.example/x")).toBe("https://other.example/x");
  });
});
