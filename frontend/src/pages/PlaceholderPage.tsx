import { Alert } from "antd";
import React from "react";

export function PlaceholderPage({ title }: { title: string }) {
  return <Alert message={title} description="该页面功能开发中。" type="info" showIcon />;
}
