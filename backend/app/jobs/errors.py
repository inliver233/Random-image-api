from __future__ import annotations


class JobPermanentError(RuntimeError):
    pass


class JobDeferError(RuntimeError):
    def __init__(self, message: str, *, run_after: str) -> None:
        super().__init__(message)
        self.run_after = run_after


class JobAbortError(RuntimeError):
    """Cooperative stop: lock lost (reclaim/cancel) or explicit abort.

    Executor must cancel the in-flight handler and must not apply a success/failure
    transition (another worker may own the row, or admin already set canceled).
    """

    def __init__(self, message: str = "job aborted", *, reason: str = "lock_lost") -> None:
        super().__init__(message)
        self.reason = str(reason or "lock_lost")
