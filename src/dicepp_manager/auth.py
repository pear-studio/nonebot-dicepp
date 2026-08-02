"""Private Manager API token compatibility facade."""

from dicepp_security.private_token import (
    TokenSecurityError,
    ensure_api_token,
    ensure_private_token,
    read_api_token,
    read_private_token,
    token_matches,
)

__all__ = [
    "TokenSecurityError",
    "ensure_api_token",
    "ensure_private_token",
    "read_api_token",
    "read_private_token",
    "token_matches",
]
