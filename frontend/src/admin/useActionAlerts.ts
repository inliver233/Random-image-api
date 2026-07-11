import React from "react";

import { messageFromError, requestIdFromError } from "./errors";

export type ActionAlertsState = {
  message: string | null;
  requestId: string | null;
  warningMessage: string | null;
  errorMessage: string | null;
  errorRequestId: string | null;
  clear: () => void;
  setSuccess: (message: string, requestId?: string | null) => void;
  setWarning: (message: string | null) => void;
  setError: (err: unknown, fallbackMessage?: string) => void;
  setErrorMessage: (message: string, requestId?: string | null) => void;
};

/**
 * Shared success/error alert state for admin mutation pages.
 */
export function useActionAlerts(): ActionAlertsState {
  const [message, setMessage] = React.useState<string | null>(null);
  const [requestId, setRequestId] = React.useState<string | null>(null);
  const [warningMessage, setWarningMessage] = React.useState<string | null>(null);
  const [errorMessage, setErrorMessageState] = React.useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = React.useState<string | null>(null);

  const clear = React.useCallback(() => {
    setMessage(null);
    setRequestId(null);
    setWarningMessage(null);
    setErrorMessageState(null);
    setErrorRequestId(null);
  }, []);

  const setSuccess = React.useCallback((msg: string, rid?: string | null) => {
    setMessage(msg);
    setRequestId(rid ? String(rid) : null);
    setWarningMessage(null);
    setErrorMessageState(null);
    setErrorRequestId(null);
  }, []);

  const setWarning = React.useCallback((msg: string | null) => {
    setWarningMessage(msg);
  }, []);

  const setError = React.useCallback((err: unknown, fallbackMessage?: string) => {
    setMessage(null);
    setRequestId(null);
    setWarningMessage(null);
    setErrorMessageState(messageFromError(err) || fallbackMessage || "操作失败");
    setErrorRequestId(requestIdFromError(err));
  }, []);

  const setErrorMessage = React.useCallback((msg: string, rid?: string | null) => {
    setMessage(null);
    setRequestId(null);
    setWarningMessage(null);
    setErrorMessageState(msg);
    setErrorRequestId(rid ? String(rid) : null);
  }, []);

  return {
    message,
    requestId,
    warningMessage,
    errorMessage,
    errorRequestId,
    clear,
    setSuccess,
    setWarning,
    setError,
    setErrorMessage,
  };
}
