from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True, kw_only=True)
class RabbitMQPayload:
    task_type: str = field(metadata={"description": "Type of the task to be processed"})
    payload: dict[str, Any] = field(metadata={"description": "Payload data for the task"})

    def to_dict(self) -> dict[str, Any]:
        """Convert the RabbitMQPayload instance to a dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary representation of the RabbitMQPayload.
        """
        return asdict(self)
