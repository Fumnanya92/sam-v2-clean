"""Sam core runtime foundation."""

__all__ = ["RequestHandler", "SamRuntime", "RuntimeSession"]


def __getattr__(name: str):
    if name == "RequestHandler":
        from .request_handler import RequestHandler

        return RequestHandler
    if name == "SamRuntime":
        from .runtime import SamRuntime

        return SamRuntime
    if name == "RuntimeSession":
        from .session import RuntimeSession

        return RuntimeSession
    raise AttributeError(name)
