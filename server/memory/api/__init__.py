"""The HTTP API the iPhone app talks to."""

from fastapi import Depends, FastAPI

from memory.api import chats, glasses, moments, people, places, settings, things, uploads
from memory.api.shared import check_token

app = FastAPI(title="Memory Glasses", dependencies=[Depends(check_token)])

for part in (moments, uploads, glasses, places, chats, people, settings, things):
    app.include_router(part.router)
