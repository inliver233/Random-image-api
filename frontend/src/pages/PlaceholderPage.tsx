import { Alert } from "antd";
import React from "react";

export function PlaceholderPage({ title }: { title: string }) {
  const isNotFound = title === "页面不存在";
  return (
    <Alert
      message={title}
      description={isNotFound ? "未找到该页面，请检查地址或从侧边栏重新进入。" : "该页面功能开发中。"}
      type={isNotFound ? "warning" : "info"}
      showIcon
    />
  );
}
