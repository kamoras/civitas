"""Which of an ActionIssue's current facts are new since its last genuine
content change — the signal behind the reader-facing "new" marker.

A brand-new issue (never updated) has nothing to diff against — every fact
being "new" would just mean the issue itself is new, not that anything
changed since a reader might have last seen it, so that case is left to the
caller to suppress (see action.py's use of this alongside `previous_facts`).
"""


def new_facts_since(current_facts: list[str], previous_facts: list[str]) -> list[str]:
    """Facts in `current_facts` whose exact text wasn't in `previous_facts`.

    Order preserved from `current_facts`; membership by exact text, not
    position — the pipeline regenerates the whole facts list on every
    genuine update, so a fact's position between versions carries no
    meaning, but the same claim reworded by the LLM would too (rewording
    isn't in scope here; that's a harder problem this doesn't attempt).
    """
    previous = set(previous_facts)
    return [f for f in current_facts if f not in previous]
