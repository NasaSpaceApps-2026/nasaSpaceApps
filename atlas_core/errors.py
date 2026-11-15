"""Errors that become readable tool results (never raw exceptions to the model).

Architecture: raised anywhere in atlas_core; devapi turns AtlasError into HTTP 422 {"error", "hint"}; mcp/ renders it as an
error tool result. Convention everywhere: a plain-sentence message plus a hint saying what to try instead."""


class AtlasError(Exception):
    """A user-correctable problem. `hint` tells the agent what to try instead."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        return {"error": self.message, "hint": self.hint}
