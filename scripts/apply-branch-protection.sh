#!/usr/bin/env bash
set -euo pipefail

REPO="mkny13/crate-cataloger"
BRANCH="main"

DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=true
      ;;
    -h|--help)
      echo "Usage: $0 [--dry-run]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 [--dry-run]" >&2
      exit 1
      ;;
  esac
done

REPO_ENDPOINT="repos/${REPO}"
PROTECTION_ENDPOINT="repos/${REPO}/branches/${BRANCH}/protection"
PROTECTION_PAYLOAD='{"required_status_checks":{"strict":false,"contexts":["test"]},"enforce_admins":false,"required_pull_request_reviews":null,"restrictions":null}'

if [ "$DRY_RUN" = true ]; then
  echo "[DRY RUN] Would ensure allow_squash_merge=true on repository:"
  echo "  Command: gh api -X PATCH ${REPO_ENDPOINT} -F allow_squash_merge=true"
  echo ""
  echo "[DRY RUN] Would apply branch protection to '${BRANCH}':"
  echo "  Endpoint: ${PROTECTION_ENDPOINT}"
  echo "  Method: PUT"
  echo "  Payload: ${PROTECTION_PAYLOAD}"
  exit 0
fi

echo "Ensuring allow_squash_merge=true on ${REPO}..."
gh api -X PATCH "${REPO_ENDPOINT}" -F allow_squash_merge=true > /dev/null

echo "Applying branch protection to ${BRANCH} on ${REPO}..."
echo "${PROTECTION_PAYLOAD}" | gh api -X PUT "${PROTECTION_ENDPOINT}" --input - > /dev/null

echo "Verifying branch protection on ${BRANCH}..."
gh api "${PROTECTION_ENDPOINT}" --jq '{required_status_checks: .required_status_checks.contexts, enforce_admins: .enforce_admins.enabled, required_pull_request_reviews: .required_pull_request_reviews}'

echo "Branch protection successfully configured on ${BRANCH}."
