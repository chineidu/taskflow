from pydantic import Field

from src.schemas.base import BaseSchema


class HealthStatusSchema(BaseSchema):
    """Health status model."""

    name: str = Field(description="Name of the API")
    status: str = Field(description="Current status of the API")
    version: str = Field(description="API version")
    database_available: bool = Field(description="Database connectivity status")
