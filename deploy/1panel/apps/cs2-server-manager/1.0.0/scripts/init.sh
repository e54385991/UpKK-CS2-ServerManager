#!/bin/sh
set -eu

# The published image runs as UID/GID 10001.  1Panel executes init.sh before
# Compose startup, so the relative persistent directory is writable on the
# first installation as well as after a rebuild.
# Generated from official installation evidence: Dockerfile USER app and
# adduser -u 10001 in https://github.com/e54385991/UpKK-CS2-ServerManager/blob/main/Dockerfile
chown -R 10001:10001 ./data
