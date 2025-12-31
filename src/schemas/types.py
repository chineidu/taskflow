from enum import StrEnum


class Environment(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class ErrorCodeEnum(StrEnum):
    HTTP_ERROR = "http_error"
    INTERNAL_SERVER_ERROR = "internal_server_error"
    INVALID_INPUT = "invalid_input"
    RESOURCES_NOT_FOUND = "resources_not_found"
    UNAUTHORIZED = "unauthorized"
    UNEXPECTED_ERROR = "unexpected_error"


class ResourcesType(StrEnum):
    """The type of resource to use."""

    CACHE = "cache"
    DATABASE = "database"
    RATE_LIMITER = "rate_limiter"
