"""SQLite storage for new plugin features; existing JSON data is unchanged."""

from .proposals import ProposalStore, proposal_scope

__all__ = ["ProposalStore", "proposal_scope"]
