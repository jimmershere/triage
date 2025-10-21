#!/bin/bash
#
#
#
podman-compose down
git pull origin main
podman-compose build || sleep 3 ; podman-compose up

