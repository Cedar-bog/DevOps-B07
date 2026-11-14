"""Classify the original prerequisite declaration, not its expanded text."""

from enum import Enum

from .makefile import Rule, _REF


class Style(str, Enum):
    DIRECT = "direct"
    MACRO = "macro"
    HYBRID = "hybrid"
    IMPLICIT = "implicit"


def identify_style(rule: Rule) -> Style:
    if rule.implicit:
        return Style.IMPLICIT
    if not _REF.search(rule.prerequisite_text):
        return Style.DIRECT
    return Style.HYBRID if _REF.sub("", rule.prerequisite_text).strip() else Style.MACRO
