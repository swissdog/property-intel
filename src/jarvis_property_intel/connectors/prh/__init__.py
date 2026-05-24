"""PRH (Finnish Patent and Registration Office) source connectors.

Provides access to the PRH open data API (YTJ) for business register
searches -- company lookups by name, business ID, or location.
"""

from .business_search import PRHBusinessSearch

__all__ = ["PRHBusinessSearch"]
