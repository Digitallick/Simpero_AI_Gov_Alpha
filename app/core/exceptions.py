class SimpleroBaseError(Exception):
    pass


class AuthenticationError(SimpleroBaseError):
    """JWT verification failed: invalid signature, expired token, or wrong audience."""

    pass


class AuthorizationError(SimpleroBaseError):
    """Authenticated user lacks permission for the requested resource."""

    pass


class TenantContextError(SimpleroBaseError):
    """org_id could not be extracted from the JWT — treated as an auth failure, not a bad request."""

    pass
