class ShortenerError(Exception):
    """Base class for domain errors."""


class AliasTakenError(ShortenerError):
    pass


class NotFoundError(ShortenerError):
    pass


class CodeGenerationError(ShortenerError):
    pass
