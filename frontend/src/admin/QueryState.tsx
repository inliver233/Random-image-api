import { Alert, Skeleton } from "antd";
import type { UseQueryResult } from "@tanstack/react-query";
import React from "react";

import { requestIdDescription } from "./errors";

type QueryLike = Pick<UseQueryResult<unknown, unknown>, "isLoading" | "isError" | "error" | "data">;

type QueryStateProps = {
  query?: QueryLike;
  /** When set, all listed queries must finish loading and none may error. */
  queries?: QueryLike[];
  errorMessage?: string;
  /** Per-query error messages aligned with `queries` (falls back to errorMessage). */
  errorMessages?: string[];
  /** When true (and data is present / multi-query ready), show empty info instead of children. */
  empty?: boolean;
  emptyMessage?: string;
  emptyDescription?: string;
  children: React.ReactNode;
};

/**
 * Shared loading / error / empty shell for admin query pages that are not cursor tables.
 * Supports a single `query` or multiple `queries` (any loading → skeleton; first error wins).
 */
export function QueryState(props: QueryStateProps) {
  const {
    query,
    queries,
    errorMessage = "加载失败",
    errorMessages,
    empty = false,
    emptyMessage,
    emptyDescription,
    children,
  } = props;

  const list = queries && queries.length > 0 ? queries : query ? [query] : [];

  if (list.length === 0) {
    return <Skeleton active />;
  }

  if (list.some((q) => q.isLoading)) {
    return <Skeleton active />;
  }

  for (let i = 0; i < list.length; i++) {
    const q = list[i];
    if (q.isError) {
      const msg = (errorMessages && errorMessages[i]) || errorMessage;
      return <Alert type="error" showIcon message={msg} description={requestIdDescription(q.error)} />;
    }
  }

  // Single-query path keeps prior "no data yet → skeleton" semantics.
  if (!queries && query && !query.data) {
    return <Skeleton active />;
  }

  if (empty) {
    return <Alert type="info" showIcon message={emptyMessage || "暂无数据"} description={emptyDescription || ""} />;
  }
  return <>{children}</>;
}
