from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True, kw_only=True)
class RabbitMQPayload:
    payload: dict[str, Any] = field(metadata={"description": "Payload data for the task"})

    def model_dump(self) -> dict[str, Any]:
        """Convert the instance to a dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary representation.
        """
        return asdict(self)


@dataclass(slots=True, kw_only=True)
class SubmittedJobResult:
    task_ids: list[str] = field(metadata={"description": "The unique identifiers for the submitted jobs."})
    number_of_messages: int = field(metadata={"description": "The number of messages submitted for the job"})


@dataclass(slots=True, kw_only=True)
class SystemHealthResult:
    messages_ready: int = field(metadata={"description": "Number of messages ready in the queue."})
    workers_online: int = field(metadata={"description": "Number of workers currently online."})


@dataclass(slots=True, kw_only=True)
class QueueArguments:
    x_dead_letter_exchange: str | None = field(
        default=None, metadata={"description": "The name of the dead-letter exchange."}
    )
    x_max_priority: int | None = field(
        default=10, metadata={"description": "The maximum priority level for the queue."}
    )
    x_expires: int | None = field(
        default=None,
        metadata={"description": "Time in milliseconds for the queue to expire after inactivity."},
    )

    def model_dump(self) -> dict[str, Any]:
        """Convert the instance to a dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary representation.
        """
        data: dict[str, Any] = {}
        if self.x_dead_letter_exchange:
            data["x-dead-letter-exchange"] = self.x_dead_letter_exchange
        if self.x_max_priority:
            data["x-max-priority"] = self.x_max_priority
        if self.x_expires:
            data["x-expires"] = self.x_expires
        return data
