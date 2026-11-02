from ..core.errors import AgentsException
from .types import JobRow

__all__ = ("AttributedPlatformFailure",)


# Carries the failed job's row out to JobDriver.run_alarm. The row can't live
# on the driver: overlapping alarm invocations dispatch jobs concurrently and
# would overwrite each other's row.
class AttributedPlatformFailure(AgentsException):
    def __init__(self, row: JobRow, cause: Exception) -> None:
        super().__init__(str(cause))
        self.row = row
        self.cause = cause
