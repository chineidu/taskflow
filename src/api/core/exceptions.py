"""Custom exceptions for the API module."""

from fastapi import status

from src.schemas.types import ErrorCodeEnum, ResourceEnum


class BaseAPIError(Exception):
    """Base exception for API-related errors."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code: str = ErrorCodeEnum.INTERNAL_SERVER_ERROR,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or self.__class__.__name__
        super().__init__(message)


class UnauthorizedError(BaseAPIError):
    """Exception raised for unauthorized access."""

    def __init__(self, details: str) -> None:
        message = f"Unauthorized access: {details}"
        self.headers = {"WWW-Authenticate": "Bearer"}
        super().__init__(
            message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=ErrorCodeEnum.UNAUTHORIZED,
        )


class HTTPError(BaseAPIError):
    """Exception raised for HTTP error."""

    def __init__(self, details: str, status_code: int = status.HTTP_503_SERVICE_UNAVAILABLE) -> None:
        message = f"HTTP error: {details}"
        super().__init__(
            message,
            status_code=status_code,
            error_code=ErrorCodeEnum.HTTP_ERROR,
        )


class ResourcesNotFoundError(BaseAPIError):
    """Exception raised when a requested resource is not found."""

    def __init__(self, resource_type: str | ResourceEnum | None = None) -> None:
        """Initialize the exception with a human-readable resource name.

        Accepts either a ResourcesType enum member, a string name (which will
        be resolved to an enum value if possible), or None.
        """

        if isinstance(resource_type, ResourceEnum):
            resource_name = str(resource_type.value)
        elif isinstance(resource_type, str):
            try:
                resource_name = str(ResourceEnum(resource_type).value)
            except Exception:
                resource_name = resource_type
        else:
            resource_name = resource_type or "unknown"
        message = f"Resource {resource_name!r} not found."
        super().__init__(
            message,
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=ErrorCodeEnum.RESOURCES_NOT_FOUND,
        )


class UnexpectedError(BaseAPIError):
    """Exception raised for unexpected errors."""

    def __init__(self, details: str) -> None:
        message = f"An unexpected error occurred: {details}"
        super().__init__(
            message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code=ErrorCodeEnum.UNEXPECTED_ERROR,
        )
