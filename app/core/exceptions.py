class SimpleroBaseError(Exception):
    pass


class AuthenticationError(SimpleroBaseError):
    """JWT verification failed: invalid signature, expired token, or wrong audience."""

    pass


class AuthorizationError(SimpleroBaseError):
    """Authenticated user lacks permission for the requested resource."""

    pass


class TenantContextError(SimpleroBaseError):
    """org_id could not be extracted from the JWT.

    Treated as an auth failure, not a bad request.
    """

    pass


class MemoryScopeError(SimpleroBaseError):
    """A retrieval-powered feature was about to search on a session that is not
    scoped to the org it is serving -- a mismatch, or an unscoped session. Refused
    fail-closed to keep one org's documents out of another org's answer.

    See app/services/memory_scope.py (AE-A-RETR-4 / SIM-241). It is a wiring
    invariant, not a client error: reaching it means a caller handed retrieval the
    wrong session, which must be surfaced loudly, not returned as an empty result.
    """

    pass
