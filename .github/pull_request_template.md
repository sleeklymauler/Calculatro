## What and why

<!-- What this changes, and why. Link the issue it fixes, if any ("Fixes #12"). -->

## Testing

<!-- How you checked it: new or changed tests, and what you tried in game. -->

## Checklist

- [ ] `pytest` and `ruff check .` pass
- [ ] Tested in game (for changes to the mod or the panel)
- [ ] Schemas bumped on both sides, if the state or odds JSON changed
- [ ] Performance tests run, if the engine or the search changed (`pytest -m perf`)
- [ ] AGENTS.md, the README or `docs/` updated, if they describe what changed
