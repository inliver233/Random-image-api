import { Alert } from "antd";
import React from "react";

type PendingAlertProps = {
  pending: boolean;
  message: string;
  style?: React.CSSProperties;
};

/**
 * Lightweight mutation-in-flight info alert used across admin forms.
 * Intentionally not a QueryState shell — mutations are not query loads.
 */
export function PendingAlert(props: PendingAlertProps) {
  const { pending, message, style } = props;
  if (!pending) return null;
  return <Alert type="info" showIcon message={message} style={style} />;
}
