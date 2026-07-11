/** Shared missing-field keys for admin image/hydration UIs. */
export const MISSING_FIELD_KEYS = [
  "tags",
  "geometry",
  "r18",
  "ai",
  "illust_type",
  "user",
  "title",
  "created_at",
  "popularity",
] as const;

export type MissingFieldKey = (typeof MISSING_FIELD_KEYS)[number];

/** Short labels for table tags / compact filters (Images page). */
export const MISSING_LABELS_SHORT: Record<MissingFieldKey, string> = {
  tags: "标签",
  geometry: "尺寸",
  r18: "R18",
  ai: "AI",
  illust_type: "类型",
  user: "作者",
  title: "标题",
  created_at: "时间",
  popularity: "热度",
};

/** Longer labels for hydration criteria summaries. */
export const MISSING_LABELS_LONG: Record<MissingFieldKey, string> = {
  tags: "标签",
  geometry: "尺寸与方向",
  r18: "R18 信息",
  ai: "AI 信息",
  illust_type: "作品类型",
  user: "作者信息",
  title: "标题",
  created_at: "发布时间",
  popularity: "热度（收藏/浏览/评论）",
};

export function missingLabel(key: string, style: "short" | "long" = "short"): string {
  const map = style === "long" ? MISSING_LABELS_LONG : MISSING_LABELS_SHORT;
  return map[key as MissingFieldKey] || key;
}
