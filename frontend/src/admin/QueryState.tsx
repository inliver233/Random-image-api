import { Alert, Skeleton } from "antd";
import type { UseQueryResult } from "@tanstack/react-query";
import React from "react";

import { requestIdDescription } from "./errors";

type QueryStateProps = {
  query: Pick<UseQueryResult<unknown, unknown>, "isLoading" | "isError" | "error" | "data">;
  errorMessage: string;
  /** When true (and data is present), show empty info instead of children. */
  empty?: boolean;
  emptyMessage?: string;
  emptyDescription?: string;
  children: React.ReactNode;
};

/**
 * Shared loading / error / empty shell for admin query pages that are not cursor tables.
 */
export function QueryState(props: QueryStateProps) {
  const { query, errorMessage, empty = false, emptyMessage, emptyDescription, children } = props;

  if (query.isLoading) {
    return <Skeleton active />;
  }
  if (query.isError) {
    return <Alert type="error" showIcon message={errorMessage} description={requestIdDescription(query.error)} />;
  }
  if (!query.data) {
    return <Skeleton active />;
  }
  if (empty) {
    return <Alert type="info" showIcon message={emptyMessage || "暂无数据"} description={emptyDescription || ""} />;
  }
  return <>{children}</>;
}
