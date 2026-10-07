from contextlib import contextmanager
from contextvars import ContextVar


_trusted_workflow_execution: ContextVar[bool] = ContextVar(
    "trusted_workflow_execution",
    default=False,
)


def is_trusted_workflow_execution() -> bool:
    return _trusted_workflow_execution.get()


@contextmanager
def trusted_workflow_execution():
    token = _trusted_workflow_execution.set(True)
    try:
        yield
    finally:
        _trusted_workflow_execution.reset(token)
