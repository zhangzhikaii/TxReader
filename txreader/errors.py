"""Errors shared by the modules; app.py turns them into HTTP responses.

A plain ValueError means the request was wrong in a way the user can fix (400, its message is
shown). NotFound means something the request names is not there (any more).
"""


class NotFound(LookupError):
    """404. Subclasses may change `detail`, the message shown to the user."""
    detail = "找不到这一项，可能已被删除，请刷新页面"
