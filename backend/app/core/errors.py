"""Application error types and their structured HTTP representation."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base error carrying an HTTP status and a stable machine-readable code."""

    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None,
                 details: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return {"error": body}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


# --- Ollama -----------------------------------------------------------------
class OllamaError(AppError):
    status_code = 502
    code = "ollama_error"


class OllamaUnavailableError(OllamaError):
    status_code = 503
    code = "ollama_unavailable"


class ModelNotFoundError(OllamaError):
    status_code = 404
    code = "model_not_found"


class ModelMemoryError(OllamaError):
    status_code = 507
    code = "insufficient_memory"
