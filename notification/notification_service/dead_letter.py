from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import aiofiles


async def write_dead_letter(entry: dict, path: str) -> None:
    record = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **entry,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    async with aiofiles.open(path, "a") as f:
        await f.write(json.dumps(record) + "\n")
