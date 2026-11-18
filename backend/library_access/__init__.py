"""B07, verified code-library access (handbook §5.2, §14).

A thin façade over B08: B06 and B14 both go through here so there is one
reader and one admitter on the library.
"""

from backend.library_access.access import LibraryAccess, AdmissionError

__all__ = ["LibraryAccess", "AdmissionError"]
