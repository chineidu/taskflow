from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True, kw_only=True)
class RabbitMQPayload:
    payload: dict[str, Any] = field(
        metadata={"description": "Payload data for the task"}
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert the RabbitMQPayload instance to a dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary representation of the RabbitMQPayload.
        """
        return asdict(self)


@dataclass(slots=True, kw_only=True)
class SubmittedJobResult:
    task_ids: list[str] = field(
        metadata={"description": "The unique identifiers for the submitted jobs."}
    )
    number_of_messages: int = field(
        metadata={"description": "The number of messages submitted for the job"}
    )
