# Archiviste Nocilia — task runner.
# Targets are POSIX-shell scripts; on Windows, run via WSL or Git Bash.

.PHONY: migrate boot-measure

# Apply all pending migrations via the disposable migrator container.
# Reads DATABASE_URL from .env.
migrate:
	docker compose --profile tools run --rm migrator

# Measure local stack boot SLA. Writes JSON to boot-metrics.json.
boot-measure:
	bash scripts/measure-boot.sh
