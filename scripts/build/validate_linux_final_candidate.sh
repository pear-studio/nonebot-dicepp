#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 4 ] || [ "$#" -gt 5 ]; then
  echo "usage: $0 TAG PACKAGE_ZIP RUNTIME_IMAGE_ID DASHBOARD_IMAGE_ID [VALIDATED_SUMMARY]" >&2
  exit 2
fi

TAG="$1"
PACKAGE_ZIP="$2"
EXPECTED_BOT_IMAGE_ID="$3"
EXPECTED_DASHBOARD_IMAGE_ID="$4"
VALIDATED_SUMMARY="${5:-}"
BOT_IMAGE="ghcr.io/pear-studio/nonebot-dicepp:${TAG}"
DASHBOARD_IMAGE="ghcr.io/pear-studio/dicepp-dashboard:${TAG}"

uv run --frozen python - "$TAG" <<'PY'
import sys

from scripts.build.release_build_metadata import validate_release_version

tag = sys.argv[1]
if not tag.startswith("v"):
    raise SystemExit("release tag must start with v")
validate_release_version(tag.removeprefix("v"))
PY
if [[ ! "$EXPECTED_BOT_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || [[ ! "$EXPECTED_DASHBOARD_IMAGE_ID" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "expected image IDs must be sha256 digests" >&2
  exit 2
fi
if [ ! -f "$PACKAGE_ZIP" ]; then
  echo "final Linux bundle is missing: $PACKAGE_ZIP" >&2
  exit 2
fi
if [ -L "$PACKAGE_ZIP" ]; then
  echo "final Linux bundle must not be a symbolic link: $PACKAGE_ZIP" >&2
  exit 2
fi
EXPECTED_PACKAGE_NAME="DicePP-${TAG}-linux-amd64.zip"
if [ "$(basename -- "$PACKAGE_ZIP")" != "$EXPECTED_PACKAGE_NAME" ]; then
  echo "final Linux bundle name differs from the release contract" >&2
  exit 2
fi

RUNNER_TEMP_INPUT="${RUNNER_TEMP:?RUNNER_TEMP must be set}"
RUNNER_TEMP_ROOT="$(realpath -e -- "$RUNNER_TEMP_INPUT")"
if [ ! -d "$RUNNER_TEMP_ROOT" ]; then
  echo "RUNNER_TEMP is not a directory" >&2
  exit 2
fi
SMOKE_ROOT_CREATED="$(mktemp -d "${RUNNER_TEMP_ROOT%/}/dicepp-linux-bundle.XXXXXX")"
if [ -L "$SMOKE_ROOT_CREATED" ] || [ ! -d "$SMOKE_ROOT_CREATED" ]; then
  echo "mktemp did not create a regular directory" >&2
  exit 2
fi
SMOKE_ROOT="$(realpath -e -- "$SMOKE_ROOT_CREATED")"
if [ "$(dirname -- "$SMOKE_ROOT")" != "$RUNNER_TEMP_ROOT" ] \
  || [[ ! "$(basename -- "$SMOKE_ROOT")" =~ ^dicepp-linux-bundle\.[[:alnum:]]{6}$ ]]; then
  echo "mktemp directory escaped RUNNER_TEMP or has an unexpected name" >&2
  exit 2
fi
SMOKE_ROOT_IDENTITY="$(stat -Lc '%d:%i' -- "$SMOKE_ROOT")"
SMOKE_PROJECT="dicepp-final-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
SMOKE_COMPOSE="${SMOKE_ROOT}/smoke-compose.json"
DIAGNOSTICS_ROOT="${CANDIDATE_DIAGNOSTICS_ROOT:-}"

validate_smoke_root_for_removal() {
  if [ -L "$SMOKE_ROOT" ] || [ ! -d "$SMOKE_ROOT" ]; then
    return 1
  fi
  local resolved parent name identity
  resolved="$(realpath -e -- "$SMOKE_ROOT")" || return 1
  parent="$(dirname -- "$resolved")"
  name="$(basename -- "$resolved")"
  identity="$(stat -Lc '%d:%i' -- "$resolved")" || return 1
  [ "$resolved" = "$SMOKE_ROOT" ] \
    && [ "$parent" = "$RUNNER_TEMP_ROOT" ] \
    && [[ "$name" =~ ^dicepp-linux-bundle\.[[:alnum:]]{6}$ ]] \
    && [ "$identity" = "$SMOKE_ROOT_IDENTITY" ]
}

capture_failure_diagnostics() {
  if [ -z "$DIAGNOSTICS_ROOT" ]; then
    return 0
  fi
  if ! mkdir -p -- "$DIAGNOSTICS_ROOT"; then
    echo "failed to create candidate diagnostics directory" >&2
    return 0
  fi
  if [ -f "$SMOKE_COMPOSE" ]; then
    if ! timeout 30s docker compose \
      --project-name "$SMOKE_PROJECT" \
      -f "$SMOKE_COMPOSE" ps --all --no-trunc \
      > "${DIAGNOSTICS_ROOT}/compose-ps.txt" 2>&1; then
      echo "failed to capture Compose process state" \
        > "${DIAGNOSTICS_ROOT}/compose-ps-error.txt"
    fi
    if ! timeout 30s docker compose \
      --project-name "$SMOKE_PROJECT" \
      -f "$SMOKE_COMPOSE" logs --no-color --timestamps \
      > "${DIAGNOSTICS_ROOT}/compose.log" 2>&1; then
      echo "failed to capture Compose logs" \
        > "${DIAGNOSTICS_ROOT}/compose-log-error.txt"
    fi
  fi
  if ! find "$SMOKE_ROOT" -maxdepth 4 -printf '%y %p %s bytes\n' \
    > "${DIAGNOSTICS_ROOT}/extracted-tree.txt" 2>&1; then
    echo "failed to capture extracted package tree" \
      > "${DIAGNOSTICS_ROOT}/extracted-tree-error.txt"
  fi
  return 0
}

cleanup() {
  local main_status=$?
  local cleanup_status=0
  trap - EXIT
  if [ "$main_status" -ne 0 ]; then
    capture_failure_diagnostics
  fi
  if [ -f "$SMOKE_COMPOSE" ]; then
    if ! timeout 60s docker compose \
      --project-name "$SMOKE_PROJECT" \
      -f "$SMOKE_COMPOSE" \
      down --volumes --remove-orphans >/dev/null 2>&1; then
      echo "failed to clean up offline Compose resources" >&2
      cleanup_status=1
    fi
  fi
  if ! timeout 15s docker info >/dev/null 2>&1; then
    echo "failed to inspect Docker state during offline smoke cleanup" >&2
    cleanup_status=1
  else
    for identity in \
      "$EXPECTED_BOT_IMAGE_ID" "$EXPECTED_DASHBOARD_IMAGE_ID" \
      "$BOT_IMAGE" "$DASHBOARD_IMAGE"; do
      if timeout 15s docker image inspect "$identity" >/dev/null 2>&1; then
        if ! timeout 60s docker image rm --force "$identity" >/dev/null 2>&1; then
          echo "failed to remove offline smoke image: $identity" >&2
          cleanup_status=1
        fi
      else
        inspect_status=$?
        if [ "$inspect_status" -eq 124 ]; then
          echo "timed out inspecting offline smoke image: $identity" >&2
          cleanup_status=1
        fi
      fi
    done
  fi
  if [ -e "$SMOKE_ROOT" ] || [ -L "$SMOKE_ROOT" ]; then
    if ! validate_smoke_root_for_removal; then
      echo "refusing to remove unverified smoke directory: $SMOKE_ROOT" >&2
      cleanup_status=1
    elif ! timeout 30s sudo --non-interactive rm -rf -- "$SMOKE_ROOT"; then
        echo "failed to remove offline smoke directory: $SMOKE_ROOT" >&2
        cleanup_status=1
    elif [ -e "$SMOKE_ROOT" ] || [ -L "$SMOKE_ROOT" ]; then
      echo "offline smoke directory still exists after cleanup: $SMOKE_ROOT" >&2
      cleanup_status=1
    fi
  fi
  if [ "$main_status" -ne 0 ]; then
    exit "$main_status"
  fi
  exit "$cleanup_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Delete all local references and content for the exact tested images. Candidate
# runs retain run-scoped tags in addition to the formal local tags, so deleting
# only the latter would let docker load appear to succeed without restoring bytes.
timeout 60s docker image rm --force \
  "$EXPECTED_BOT_IMAGE_ID" "$EXPECTED_DASHBOARD_IMAGE_ID" >/dev/null
timeout 15s docker info >/dev/null
for identity in \
  "$BOT_IMAGE" "$DASHBOARD_IMAGE" \
  "$EXPECTED_BOT_IMAGE_ID" "$EXPECTED_DASHBOARD_IMAGE_ID"; do
  if timeout 15s docker image inspect "$identity" >/dev/null 2>&1; then
    echo "pre-package image identity still exists before offline round trip: $identity" >&2
    exit 1
  else
    inspect_status=$?
    if [ "$inspect_status" -eq 124 ]; then
      echo "timed out asserting image identity absence: $identity" >&2
      exit 1
    fi
  fi
done

unzip -q "$PACKAGE_ZIP" -d "$SMOKE_ROOT"
(
  cd "$SMOKE_ROOT"
  sha256sum -c checksums.sha256
)
IMAGE_ARCHIVE_ZST="$(
  uv run --frozen python scripts/build/validate_linux_bundle_candidate.py \
    --package-root "$SMOKE_ROOT" \
    --manifest "${SMOKE_ROOT}/dicepp-package.json" \
    --expected-image bot "$BOT_IMAGE" "$EXPECTED_BOT_IMAGE_ID" \
    --expected-image dashboard "$DASHBOARD_IMAGE" "$EXPECTED_DASHBOARD_IMAGE_ID"
)"
IMAGE_ARCHIVE="${IMAGE_ARCHIVE_ZST%.zst}"
zstd -d "$IMAGE_ARCHIVE_ZST" -o "$IMAGE_ARCHIVE"
timeout 120s docker load -i "$IMAGE_ARCHIVE"

LOADED_BOT_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$BOT_IMAGE")"
LOADED_DASHBOARD_IMAGE_ID="$(
  docker image inspect --format '{{.Id}}' "$DASHBOARD_IMAGE"
)"
if [ "$LOADED_BOT_IMAGE_ID" != "$EXPECTED_BOT_IMAGE_ID" ]; then
  echo "offline Runtime Image ID differs from the tested candidate" >&2
  exit 1
fi
if [ "$LOADED_DASHBOARD_IMAGE_ID" != "$EXPECTED_DASHBOARD_IMAGE_ID" ]; then
  echo "offline Dashboard Image ID differs from the tested candidate" >&2
  exit 1
fi

RESOLVED_COMPOSE="${SMOKE_ROOT}/resolved-compose.json"
DICEPP_IMAGE_TAG="$TAG" docker compose \
  --project-name "$SMOKE_PROJECT" \
  -f "${SMOKE_ROOT}/docker-compose.yml" \
  config --format json > "$RESOLVED_COMPOSE"
uv run --frozen python - \
  "$RESOLVED_COMPOSE" \
  "$SMOKE_COMPOSE" \
  "$BOT_IMAGE" \
  "$DASHBOARD_IMAGE" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
bot_image, dashboard_image = sys.argv[3:]
compose = json.loads(source.read_text(encoding="utf-8"))
compose.get("networks", {}).pop("dice-net", None)
services = compose["services"]
if services["bot"]["image"] != bot_image:
    raise SystemExit("Compose Runtime service does not use the Runtime image")
for role in ("dashboard", "manager"):
    if services[role]["image"] != dashboard_image:
        raise SystemExit(
            f"Compose {role} service does not use the Dashboard/Manager image"
        )
services["bot"].get("networks", {}).pop("dice-net", None)
for service in services.values():
    for key in ("build", "container_name", "ports"):
        service.pop(key, None)
    service["restart"] = "no"
services["manager"]["environment"]["DICEPP_MANAGER_RELEASE_SCHEDULER"] = "false"
destination.write_text(
    json.dumps(compose, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

mapfile -t COMPOSE_IMAGES < <(
  docker compose \
    --project-name "$SMOKE_PROJECT" \
    -f "$SMOKE_COMPOSE" \
    config --images | sort -u
)
if [ "${COMPOSE_IMAGES[*]}" != "$DASHBOARD_IMAGE $BOT_IMAGE" ]; then
  echo "offline Compose does not resolve to the two loaded release images" >&2
  printf 'resolved image: %s\n' "${COMPOSE_IMAGES[@]}" >&2
  exit 1
fi

timeout 210s docker compose \
  --project-name "$SMOKE_PROJECT" \
  -f "$SMOKE_COMPOSE" \
  up -d --pull never --wait --wait-timeout 180
for service in bot dashboard manager; do
  if [ -z "$(
    docker compose \
      --project-name "$SMOKE_PROJECT" \
      -f "$SMOKE_COMPOSE" \
      ps --status running -q "$service"
  )" ]; then
    echo "offline Compose service is not running: $service" >&2
    exit 1
  fi
done
for service in dashboard manager; do
  CONTAINER_ID="$(
    docker compose \
      --project-name "$SMOKE_PROJECT" \
      -f "$SMOKE_COMPOSE" \
      ps -q "$service"
  )"
  HEALTH="$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_ID")"
  if [ "$HEALTH" != "healthy" ]; then
    echo "offline Compose service is not healthy: $service ($HEALTH)" >&2
    exit 1
  fi
done

if [ -n "$VALIDATED_SUMMARY" ]; then
  SUMMARY_PARENT="$(dirname -- "$VALIDATED_SUMMARY")"
  mkdir -p -- "$SUMMARY_PARENT"
  SUMMARY_TEMP="$(mktemp "${SUMMARY_PARENT%/}/.validated-linux.XXXXXX")"
  PACKAGE_SIZE="$(stat -Lc '%s' -- "$PACKAGE_ZIP")"
  PACKAGE_SHA256="$(sha256sum -- "$PACKAGE_ZIP" | cut -d ' ' -f 1)"
  if [[ ! "$PACKAGE_SIZE" =~ ^[1-9][0-9]*$ ]] \
    || [[ ! "$PACKAGE_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "failed to declare validated Linux bundle identity" >&2
    exit 1
  fi
  printf '{\n  "contract_version": 1,\n  "artifacts": [\n    {"filename": "%s", "size": %s, "sha256": "%s"}\n  ]\n}\n' \
    "$EXPECTED_PACKAGE_NAME" "$PACKAGE_SIZE" "$PACKAGE_SHA256" > "$SUMMARY_TEMP"
  mv -f -- "$SUMMARY_TEMP" "$VALIDATED_SUMMARY"
fi
