"""Online requests cannot silently restart as a different recall."""

RECALL_TRANSITIONS = {
    "accepted": frozenset({"running", "failed"}),
    "running": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
}
