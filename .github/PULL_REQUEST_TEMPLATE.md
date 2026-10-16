## Summary

<!-- What does this PR do and why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature / data source
- [ ] Score / pipeline change
- [ ] Refactor (no behavior change)
- [ ] Docs / config only

## Test plan

<!-- How did you verify this works? Include senator/rep names, bill IDs, or
     specific pipeline stages if relevant. -->

## Checklist

- [ ] `npm run lint` passes (frontend changes)
- [ ] `pytest tests/ -m "not slow"` passes (backend changes)
- [ ] No secrets, API keys, or `.db` files committed
- [ ] Score changes: ran the audit skill and reviewed output
- [ ] Pipeline changes: tested against a real API response, not just mocks
- [ ] New page or feature: entry added to `backend/app/data/whats_new.json` (it is the changelog line, the feed entry and the Bluesky post)
