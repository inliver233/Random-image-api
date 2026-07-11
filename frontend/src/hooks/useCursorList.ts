import React from "react";
import { useMutation, useQuery, type QueryKey } from "@tanstack/react-query";

/**
 * Cursor-paginated list with generation counter.
 * Prevents a stale load-more response from appending into a filter that already changed.
 */
export function useCursorList<TItem, TResponse extends { items: TItem[]; next_cursor?: string | null; request_id?: string }>(options: {
  queryKey: QueryKey;
  fetchPage: (cursor: string | null) => Promise<TResponse>;
  getItemId: (item: TItem) => string;
  enabled?: boolean;
}) {
  const { queryKey, fetchPage, getItemId, enabled = true } = options;
  const [items, setItems] = React.useState<TItem[]>([]);
  const [nextCursor, setNextCursor] = React.useState("");
  const [listRequestId, setListRequestId] = React.useState<string | null>(null);
  const generationRef = React.useRef(0);
  const queryKeyRef = React.useRef(queryKey);

  const query = useQuery({
    queryKey,
    queryFn: () => fetchPage(null),
    enabled,
  });

  React.useEffect(() => {
    // New filter/queryKey → bump generation so in-flight load-more is ignored,
    // and clear the list so stale items from the previous filter don't flash.
    const prev = queryKeyRef.current;
    queryKeyRef.current = queryKey;
    if (JSON.stringify(prev) !== JSON.stringify(queryKey)) {
      generationRef.current += 1;
      setItems([]);
      setNextCursor("");
      setListRequestId(null);
    }
  }, [queryKey]);

  React.useEffect(() => {
    if (!query.data) return;
    generationRef.current += 1;
    setItems(query.data.items);
    setNextCursor(query.data.next_cursor || "");
    setListRequestId(query.data.request_id ? String(query.data.request_id) : null);
  }, [query.data]);

  const loadMore = useMutation({
    mutationFn: async (cursor: string) => {
      const gen = generationRef.current;
      const data = await fetchPage(cursor);
      return { data, gen };
    },
    onSuccess: ({ data, gen }) => {
      if (gen !== generationRef.current) {
        // Stale page for a previous filter — drop it.
        return;
      }
      setItems((prev) => {
        const seen = new Set(prev.map((x) => getItemId(x)));
        const merged = [...prev];
        for (const item of data.items) {
          const id = getItemId(item);
          if (!seen.has(id)) {
            seen.add(id);
            merged.push(item);
          }
        }
        return merged;
      });
      setNextCursor(data.next_cursor || "");
      setListRequestId(data.request_id ? String(data.request_id) : null);
    },
  });

  return {
    query,
    items,
    nextCursor,
    listRequestId,
    loadMore,
    setItems,
    hasMore: Boolean(nextCursor),
    isLoadingMore: loadMore.isPending,
  };
}
