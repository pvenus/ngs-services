class ExecutionError(RuntimeError):
    status = 400


class ValidationError(ExecutionError):
    status = 422


class NotFoundError(ExecutionError):
    status = 404


class ConflictError(ExecutionError):
    status = 409
