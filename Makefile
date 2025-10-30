.PHONY: bootstrap doctor build up down clean

bootstrap:
	bash scripts/podman-rootless-bootstrap.sh

doctor:
	bash scripts/podman-doctor.sh

build:
	podman-compose build

up:
	podman-compose up -d

down:
	podman-compose down

clean:
	podman system prune -af
