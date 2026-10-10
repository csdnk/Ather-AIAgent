"""AET-42 observations of forbidden discovery fallbacks; providers always delegate."""

from inspect import iscoroutinefunction


def observe_discovery(runtime, monkeypatch):
    calls = []
    providers = (
        (runtime.recall.memories, ("working", "load", "history")),
        (runtime.recall.assembly.bodies, ("load_bodies",)),
        (
            runtime.vectors.client,
            ("query", "query_iterator", "hybrid_search", "get", "search_iterator"),
        ),
    )
    seen = set()
    for provider, names in providers:
        for name in names:
            if (id(provider), name) in seen or not callable(getattr(provider, name, None)):
                continue
            seen.add((id(provider), name))
            original = getattr(provider, name)

            def record(args, name=name):
                calls.append(
                    {
                        "method": name,
                        "operation_id": getattr(args[0], "operation_id", None) if args else None,
                    }
                )

            if iscoroutinefunction(original):

                async def observed(*args, _original=original, _record=record, **kwargs):
                    _record(args)
                    return await _original(*args, **kwargs)
            else:

                def observed(*args, _original=original, _record=record, **kwargs):
                    _record(args)
                    return _original(*args, **kwargs)

            monkeypatch.setattr(provider, name, observed)
    return calls


def assert_no_fallback(calls, operation_id):
    forbidden = [
        c
        for c in calls
        if c["operation_id"] == operation_id
        or c["method"] in {"query", "query_iterator", "hybrid_search", "get", "search_iterator"}
    ]
    assert not forbidden, "vector failure triggered another candidate/body discovery path"
