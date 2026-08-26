## Introduction

CS2 Server Manager is a FastAPI-based Counter-Strike 2 server panel. It manages multiple
game servers over SSH and provides deployment, lifecycle, monitoring, plugin, and live
console features.

Copy this directory as a complete package to `/opt/1panel/resource/apps/local/cs2-server-manager/`.
The app root must contain `data.yml`, `logo.png`, and `1.0.0/data.yml`; do not copy only the
`1.0.0` directory.

## Features

- Manage multiple CS2 servers from a web interface;
- Deploy, start, stop, and restart servers over SSH;
- Install Metamod:Source and CounterStrikeSharp plugin frameworks;
- Redis status caching, PostgreSQL persistence, and WebSocket updates.
