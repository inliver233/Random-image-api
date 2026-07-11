/** Shared display formatters for admin tables. */

export function yesNo(value: unknown): string {
  return value ? "是" : "否";
}

/** Null/empty → "-" for table cells. */
export function dash(value: unknown): string {
  return value ? String(value) : "-";
}
