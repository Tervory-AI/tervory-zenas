"""Distinct offline judgments for tests of answer diagnostics and scheduling."""

import json


def distinct_library(documents):
    return [{**documents[index % len(documents)],
             "name": documents[index % len(documents)]["name"] if index < len(documents)
                     else f"Placeholder Buyer {index} v Placeholder Supplier {index}",
             "citation": documents[index % len(documents)]["citation"] if index < len(documents)
                         else f"placeholder-citation-{index}"}
            for index in range(10)]


def recite(result, documents, index):
    """Keep a diagnostic fake's deliberate error while naming the distinct case asked."""
    if index < 40 or not isinstance(result, dict):
        return result
    old = documents[(index - 40) % len(documents)]["citation"]
    new = distinct_library(documents)[index - 40]["citation"]
    return json.loads(json.dumps(result).replace(old, new))
