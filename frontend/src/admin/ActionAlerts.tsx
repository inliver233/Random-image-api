import { Alert, Typography } from "antd";
import React from "react";

type ActionAlertsProps = {
  message?: string | null;
  requestId?: string | null;
  errorMessage?: string | null;
  errorRequestId?: string | null;
  warningMessage?: string | null;
  /** Where to show request IDs: Alert description or secondary text (default). */
  requestIdPlacement?: "description" | "secondary";
  /** When false, omit request-id lines (default true). */
  showRequestId?: boolean;
};

/**
 * Success / warning / error Alert pair for admin mutation feedback.
 */
export function ActionAlerts(props: ActionAlertsProps) {
  const {
    message,
    requestId,
    errorMessage,
    errorRequestId,
    warningMessage,
    requestIdPlacement = "secondary",
    showRequestId = true,
  } = props;

  const successDesc =
    showRequestId && requestIdPlacement === "description" && requestId ? `请求ID: ${requestId}` : "";
  const errorDesc =
    showRequestId && requestIdPlacement === "description" && errorRequestId ? `请求ID: ${errorRequestId}` : "";
  const showSecondaryRequestId = showRequestId && requestIdPlacement === "secondary";

  return (
    <>
      {message ? <Alert type="success" showIcon message={message} description={successDesc} /> : null}
      {showSecondaryRequestId && message && requestId ? (
        <Typography.Text type="secondary">请求ID: {requestId}</Typography.Text>
      ) : null}
      {warningMessage ? <Alert type="warning" showIcon message={warningMessage} /> : null}
      {errorMessage ? <Alert type="error" showIcon message={errorMessage} description={errorDesc} /> : null}
      {showSecondaryRequestId && errorMessage && errorRequestId ? (
        <Typography.Text type="secondary">请求ID: {errorRequestId}</Typography.Text>
      ) : null}
    </>
  );
}
