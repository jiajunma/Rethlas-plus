---
description: List prioritised open work items for a rethlas-kb project
allowed-tools: Bash(rethlas-kb:*)
---

List the open questions (closure members that aren't admitted yet)
for a rethlas-kb project, sorted by distance from the nearest goal
(closer = higher priority).

```bash
rethlas-kb open-questions --project $ARGUMENTS
```

Optional flags:
- `--limit N` — only show the top N items
- `--json` — machine-readable
- `--blueprint <path>` — if not in cwd

Once you've picked an item from the list, take action with the
single-node form of the corresponding agent (e.g. `/verify-stmt
<node-id>`, `/fill-gap <node-id>`).

Missing nodes (referenced by `uses:` but not in the KB) appear
flagged so you can decide whether to stage them or update the
referring node.
