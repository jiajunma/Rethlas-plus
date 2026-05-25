---
description: Show counts dashboard for a rethlas-kb project (goals / closure / admitted / staged / missing)
allowed-tools: Bash(rethlas-kb:*)
---

Show a rethlas-kb project's status dashboard.

```bash
rethlas-kb status --project $ARGUMENTS
```

Add `--json` if you want machine-readable output instead of the
table. Pass `--blueprint <path>` if the blueprint isn't in the
current directory.

The dashboard reports:
- closure size (transitive `uses:` from goal_nodes)
- admitted / staged / missing counts
- done ratio (admitted / closure)
- breakdown by status and kind

Use this to see "how much of the project is proved" at a glance.
