import { Alert, Button, Card, Skeleton, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import React from "react";

import { requestIdDescription } from "./errors";

type CursorTableCardProps<TItem> = {
  columns: ColumnsType<TItem>;
  items: TItem[];
  rowKey: (row: TItem) => string;
  query: UseQueryResult<unknown, unknown>;
  loadMore: UseMutationResult<unknown, unknown, string, unknown>;
  nextCursor: string;
  listRequestId?: string | null;
  errorMessage: string;
  emptyMessage: string;
  emptyDescription?: string;
  scrollX?: number;
  /** Optional Card title for embedded list sections. */
  title?: string;
};

/**
 * Shared Card+Table+load-more shell for simple admin/public cursor lists.
 */
export function CursorTableCard<TItem>(props: CursorTableCardProps<TItem>) {
  const {
    columns,
    items,
    rowKey,
    query,
    loadMore,
    nextCursor,
    listRequestId,
    errorMessage,
    emptyMessage,
    emptyDescription,
    scrollX,
    title,
  } = props;

  return (
    <>
      {loadMore.isError ? (
        <Alert
          type="error"
          showIcon
          message={loadMore.error instanceof Error ? loadMore.error.message : "加载更多失败"}
          style={{ marginBottom: 12 }}
        />
      ) : null}

      {query.isLoading ? (
        <Skeleton active />
      ) : query.isError ? (
        <Alert type="error" showIcon message={errorMessage} description={requestIdDescription(query.error)} />
      ) : !query.data ? (
        <Skeleton active />
      ) : items.length === 0 ? (
        <Alert type="info" showIcon message={emptyMessage} description={emptyDescription || ""} />
      ) : (
        <Card title={title}>
          {listRequestId ? <Typography.Text type="secondary">请求ID: {listRequestId}</Typography.Text> : null}
          <Table<TItem>
            rowKey={rowKey}
            columns={columns}
            dataSource={items}
            pagination={false}
            size="small"
            scroll={scrollX ? { x: scrollX } : undefined}
            style={{ marginTop: 12 }}
          />
          {nextCursor ? (
            <div style={{ marginTop: 12 }}>
              <Button onClick={() => loadMore.mutate(nextCursor)} loading={loadMore.isPending}>
                加载更多
              </Button>
            </div>
          ) : null}
        </Card>
      )}
    </>
  );
}
