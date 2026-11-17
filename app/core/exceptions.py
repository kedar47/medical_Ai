class OrderError(Exception):
    def __init__(
        self,
        status_code: int,
        detail: str,
        extra: dict[str, object] | None = None,
    ):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.extra = extra or {}


class VoiceError(Exception):
    def __init__(
        self,
        status_code: int,
        detail: str,
        extra: dict[str, object] | None = None,
    ):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.extra = extra or {}
