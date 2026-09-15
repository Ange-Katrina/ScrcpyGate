"""Shared storage-domain exception types.

This module deliberately has no database or application imports.  Storage
owners and the compatibility facade can therefore share exception identity
without introducing a reverse import from a domain owner into ``storage``.
"""

from __future__ import annotations


class AlasConfigOwnershipError(ValueError):
    """Raised when an ALAS config is already assigned to another user."""

    code = "alas_config_already_assigned"

    def __init__(self, config_name: str, owner: str):
        self.config_name = config_name
        self.owner = owner
        super().__init__(f'ALAS config "{config_name}" is already assigned to user "{owner}"')


__all__ = ["AlasConfigOwnershipError"]
