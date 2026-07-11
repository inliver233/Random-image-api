import { Tag } from "antd";
import React from "react";

export function jobStatusLabel(status: string): string {
  switch (status) {
    case "pending":
      return "等待中";
    case "running":
      return "运行中";
    case "paused":
      return "已暂停";
    case "canceled":
      return "已取消";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "dlq":
      return "死信";
    default:
      return status || "未知";
  }
}

export function jobStatusColor(status: string): string {
  switch (status) {
    case "running":
      return "processing";
    case "pending":
      return "blue";
    case "completed":
      return "success";
    case "failed":
      return "error";
    case "paused":
      return "warning";
    case "dlq":
      return "magenta";
    case "canceled":
      return "default";
    default:
      return "default";
  }
}

export function jobStatusTag(status: string): React.ReactElement {
  return <Tag color={jobStatusColor(status)}>{jobStatusLabel(status)}</Tag>;
}

export function jobTypeLabel(type: string): string {
  switch (type) {
    case "import_images":
      return "导入图片";
    case "hydrate_metadata":
      return "补全元数据";
    case "proxy_probe":
      return "代理探测";
    case "easy_proxies_import":
      return "导入 easy-proxies";
    case "heal_url":
      return "修复 URL";
    default:
      return type || "未知";
  }
}
