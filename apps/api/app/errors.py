from fastapi import HTTPException


def fail(code: str, message: str, status: int = 422, **details):
    raise HTTPException(status, {"code": code, "message": message, "details": details})


class ReviewError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, unknown_remote: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.unknown_remote = unknown_remote
