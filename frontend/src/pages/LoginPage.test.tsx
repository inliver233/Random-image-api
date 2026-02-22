import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAdminToken } from "../auth/tokenStorage";
import { LoginPage } from "./LoginPage";

describe("LoginPage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/admin/api/login")) {
          return new Response(JSON.stringify({ ok: true, token: "tok_admin", request_id: "req_login" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({ ok: false, code: "NOT_FOUND", message: "not found", request_id: "req_x", details: {} }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  it("logs in and stores token", async () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByPlaceholderText("请输入用户名"), { target: { value: "admin" } });
    fireEvent.change(screen.getByPlaceholderText("请输入密码"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));

    await waitFor(() => {
      expect(getAdminToken()).toBe("tok_admin");
    });
    expect(await screen.findByText(/请求ID:\s*req_login/)).toBeInTheDocument();
    expect(screen.queryByText("tok_admin")).not.toBeInTheDocument();
  });

  it("shows error and request_id on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/admin/api/login")) {
          return new Response(
            JSON.stringify({ ok: false, code: "UNAUTHORIZED", message: "bad", request_id: "req_bad", details: {} }),
            { status: 401, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify({ ok: false, code: "NOT_FOUND", message: "not found", request_id: "req_x", details: {} }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByPlaceholderText("请输入用户名"), { target: { value: "admin" } });
    fireEvent.change(screen.getByPlaceholderText("请输入密码"), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));

    expect(await screen.findByText(/请求ID:\s*req_bad/)).toBeInTheDocument();
    expect(getAdminToken()).toBeNull();
  });
});


