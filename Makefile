.PHONY: bootstrap doctor build up down clean

PODMAN_COMPOSE := tools/podman_compose.sh

bootstrap:
	bash scripts/podman-rootless-bootstrap.sh

doctor:
	bash scripts/podman-doctor.sh

build:
	$(PODMAN_COMPOSE) build

up:
	$(PODMAN_COMPOSE) up -d

down:
	$(PODMAN_COMPOSE) down

clean:
	podman system prune -af
