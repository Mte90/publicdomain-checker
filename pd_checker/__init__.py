"""Italian public-domain checker: deterministic evaluation of L. 633/1941.

Import the rules module and call ``evaluate`` (or ``evaluate_many``) with facts
extracted from an image::

    from pd_checker import rules
    verdict = rules.evaluate(facts, current_year=2026)
"""

from .core import rules
from .core.rules import (
    NOT_PUBLIC_DOMAIN,
    PUBLIC_DOMAIN,
    RULE_TABLE_VERSION,
    UNCERTAIN,
    evaluate,
    evaluate_many,
)

__all__ = [
    "rules",
    "PUBLIC_DOMAIN",
    "NOT_PUBLIC_DOMAIN",
    "UNCERTAIN",
    "RULE_TABLE_VERSION",
    "evaluate",
    "evaluate_many",
]
