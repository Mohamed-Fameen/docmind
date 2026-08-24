"""
Shared citation-parsing logic. Extracted into its own module (rather than living only in
main.py) because both the API layer (main.py, for the cited/uncited source flags) and the
agent's confidence check (agent.py, to decide whether to retry) need the same logic, and
duplicating a regex in two places is how they quietly drift out of sync over time.
"""

import re

# Handles both single citations ([2]) and grouped ones ([2, 3]) — an earlier version only
# matched single-number brackets and silently missed grouped citations, which the model
# produces regularly in practice. Caught by testing against a real logged answer.
CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def extract_cited_numbers(answer: str) -> set[int]:
    """Parses citation numbers out of answer text, e.g. '[2]' or '[2, 3]' -> {2, 3}."""
    numbers = set()
    for match in CITATION_RE.findall(answer):
        numbers.update(int(n.strip()) for n in match.split(","))
    return numbers
