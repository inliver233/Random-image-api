from __future__ import annotations


class JobPermanentError(RuntimeError):
    pass


class JobDeferError(RuntimeError):
    def __init__(self, message: str, *, run_after: str) -> None:
        super().__init__(message)
        self.run_after = run_after
