"""The web app's endpoints, one router per part of the app; app.py mounts them.

Errors are turned into responses in app.py: a ValueError from the modules becomes 400 with its
message, so the endpoints here don't catch it themselves.
"""
from pydantic import BaseModel


class IdsBody(BaseModel):
    ids: list[int]
