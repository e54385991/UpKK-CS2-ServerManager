# Frontend console

The operator console is the **Next.js 16.3.5** app in `frontend/`. It talks to
FastAPI through same-origin rewrites (`/api/*`, `/health`, `/static/*`).

FastAPI no longer ships a Jinja/Bootstrap HTML console. Leftover HTML paths
return 404. Plugin archives and host setup libraries still live under
`static/uploads/` and `static/linux_lib/`.

See `frontend/AGENTS.md` for stack, layout, and commands.
