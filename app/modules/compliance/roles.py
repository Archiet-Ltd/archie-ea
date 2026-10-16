"""The roles that may change compliance records: record control tests, assign evidence
owners, write and publish policies, and run a compliance check.

These are the roles the framework adopt endpoint accepts plus the compliance persona
(``compliance`` is the enterprise role; ``compliance_officer`` is the role name the
governance routes already pass). One tuple, so every compliance write route admits the
same people.
"""

COMPLIANCE_WRITE_ROLES = (
    "admin",
    "architect",
    "business_architect",
    "security_architect",
    "compliance",
    "compliance_officer",
)
