
from dataclasses import dataclass


@dataclass
class ConfirmationGuard:
    pending_correction: bool = False
    summary_read_back: bool = False
    summary_version: str | None = None

    def correction_started(self):
        self.pending_correction = True
        self.summary_read_back = False
        self.summary_version = None

    def correction_completed(self):
        self.pending_correction = False
        self.summary_read_back = False
        self.summary_version = None

    def summary_presented(self, version: str):
        if self.pending_correction:
            return False

        self.summary_read_back = True
        self.summary_version = version
        return True

    def can_confirm(self, current_version: str) -> bool:
        return (
            not self.pending_correction
            and self.summary_read_back
            and self.summary_version == current_version
        )
