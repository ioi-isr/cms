#!/usr/bin/env bash
set -x

docker compose -p cms -f docker/docker-compose.dev.yml run --build --rm --service-ports devcms
