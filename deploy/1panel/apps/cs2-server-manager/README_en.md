## Introduction

CS2 Server Manager is a Counter-Strike 2 server panel. One store app starts
Caddy, the Next.js console, and FastAPI together. Caddy is the public root;
FastAPI stays on the private `app:8000` `/api` listener. Do not install the
frontend and backend as two 1Panel runtimes.

Copy this directory as a complete package to `/opt/1panel/resource/apps/local/cs2-server-manager/`.
The app root must contain `data.yml`, `logo.png`, and `1.0.0/data.yml`; do not copy only the
`1.0.0` directory.

After install, open `http://SERVER_IP:31800` (or the console port you chose).
Default login is `admin` / `admin123`. FastAPI stays on container port
`8000` — do not remap it to `8001`. For a second instance use console
port `31801` and set the browser origin to that URL, not `0.0.0.0`.

## Features

- Manage multiple CS2 servers from a web interface;
- Deploy, start, stop, and restart servers over SSH;
- Install Metamod:Source and CounterStrikeSharp plugin frameworks;
- Redis status caching, PostgreSQL persistence, and WebSocket updates.
