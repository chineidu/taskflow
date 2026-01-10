import hashlib
from typing import Any

from src.utilities import msgspec_encoder, sort_dict


def generate_idempotency_key(payload: dict[str, Any], user_id: str | None = None) -> str:
    """Generate an idempotency key based on the payload and optional user ID."""
    hasher = hashlib.sha256()
    if user_id:
        data: dict[str, Any] = {"payload": payload, "user_id": user_id}
    else:
        data = {"payload": payload}

    # Serialize the payload using msgspec for consistent hashing
    serialized_payload = msgspec_encoder.encode(sort_dict(data))
    hasher.update(serialized_payload)

    return hasher.hexdigest()
