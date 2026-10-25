# Tested container releases

Releases are manual. The `release.yml` workflow accepts a full source commit and
a stable `vMAJOR.MINOR.PATCH` version, and runs only from trusted `main`. It does
not deploy an office. A version is single-use; a failed partial publication needs
operator inspection, never an automatic overwrite or rebuild of that version.

The exact source must be an ancestor of the dispatch commit, have the same
workflow tree, and have successful **push-to-main** runs of every job listed in
`.github/release-checks.json`. A skipped job, foreign repository, PR run, missing
job or newer failed/in-progress attempt cannot satisfy the gate. Branch
protection should require the corresponding Python, Docker and browser checks.

Each architecture is built once on its native runner. The workflow forms one
index, pulls it by digest and tests both architectures against the expected
`/version` identity. Only then does it add the version tag to that same index.
The release's `release.json` records the index digest, both platform manifest
digests, source SHA, version and data schema. The GitHub release is published
last. Consumers use the digest, never a mutable `latest` tag. OCI labels and
`/version` come from build arguments; runtime environment cannot override them.
Local source runs report `dev` and a null revision.

The two native builds call the same reusable build workflow separately. Each
returns the exact uploaded artifact ID for its platform digest. The index job
downloads those IDs and returns its own descriptor artifact ID to promotion and
publication. Consumers never infer an artifact's name from their current run
attempt. If only one platform build fails, **Re-run failed jobs** can reuse the
other platform's successful output. A retry after smoke or promotion failure
reuses the already tested candidate and descriptor without rebuilding it.

Artifacts are retained for seven days. Retry only while the producer artifacts
remain available; missing artifacts fail the release. A failure after tag or draft
release creation still needs operator inspection under the single-use version
policy above. Artifact retry support does not make partial publication automatic
or permit replacing an existing release.

## One-time repository administration

These are future administrator actions, not performed by running repository tests:

1. Enable immutable GitHub releases and protect `main` with the required checks.
2. Permit the release workflow to publish this repository's GHCR package. Make
   the package public for credential-free Pi updates; private packages require
   an operator-managed read-only Docker login on the host.
3. Provide a release-only `RELEASE_POLICY_TOKEN` that can read this repository's
   **Administration** settings. Prefer a short-lived GitHub App installation
   token with only `Administration: read`; renew it before dispatch. It is used
   only to query `GET /repos/{owner}/{repo}/immutable-releases`. The standard
   `GITHUB_TOKEN` cannot receive this permission. Without this credential the
   workflow fails closed before building. Do not provide a broad write PAT.

The workflow checks immutability before building and immediately before
publication, then verifies the published response is immutable. GitHub does not
offer an atomic `require_immutable` publish parameter: administrators must not
disable the setting during publication. Updaters also reject mutable releases.

Job permissions are separated: check has contents/actions read; build, index and
promote have packages write; smoke has packages read; only final publication has
contents write. Pull-request checks have no publishing credentials. Registry
promotion stops on transport/authentication errors and only treats an explicit
`MANIFEST_UNKNOWN` response as a missing version. GHCR tags have no create-only
transaction, so maintainers must reserve release tags for this serialized
workflow. The immutable GitHub asset and its digest are the consumer authority.

## Verification

Run `python3 -B tests/test_release.py`, `python3 -B tests/test_release_workflow.py`
and `actionlint`. The workflow tests resolve the actual producer/consumer output
bindings using synthetic partial-rerun state; they do not invoke GitHub Actions.
Test a built image with:

```sh
docker build --build-arg TOKEN_TALLY_VERSION=v1.2.3 \
  --build-arg TOKEN_TALLY_REVISION=<full-commit-sha> -t token-tally-test .
python3 -B tests/test_docker_smoke.py --image token-tally-test \
  --platform linux/arm64 --expected-version v1.2.3 \
  --expected-revision <full-commit-sha>
```

Tests use isolated data and never publish a release. A real manual workflow run
with repository permissions and immutable releases enabled remains an operational
acceptance check. The tested release artifact is its recorded digest; this is
not a byte-for-byte reproducible-build claim.
The Dockerfile pins one multi-platform Python base index for both architectures;
review base-image updates regularly and rerun both smoke jobs when changing it.

References: [immutable-release settings API](https://docs.github.com/en/rest/repos/repos#check-if-immutable-releases-are-enabled-for-a-repository),
[workflow permissions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions),
[release publication API](https://docs.github.com/en/rest/releases/releases#update-a-release).
