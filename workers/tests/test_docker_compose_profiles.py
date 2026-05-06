"""AC-13 contract: fake-gcs-server must stay behind the `tools` profile.

Without `profiles: ["tools"]`, `docker compose up -d` would start the emulator
in the default stack and silently shadow the real GCS bucket in dev.
"""

from pathlib import Path

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def test_gcs_service_is_gated_by_tools_profile() -> None:
    lines = COMPOSE_FILE.read_text(encoding="utf-8").splitlines()
    gcs_index = next(
        (i for i, line in enumerate(lines) if line.rstrip() == "  gcs:"),
        None,
    )
    assert gcs_index is not None, "service `gcs` missing from docker-compose.yml"

    block = "\n".join(lines[gcs_index : gcs_index + 10])
    assert 'profiles: ["tools"]' in block, (
        'service `gcs` must declare `profiles: ["tools"]` '
        "so it is excluded from `docker compose up -d`"
    )
