# Versioning and the API contract

FWS is pre-1.0. This document says what that means for something you build
against it, so "the interface may change" stops being a shrug and becomes a
promise you can plan around.

## What is under contract

The REST surface at `/api/v1`, as described by the OpenAPI spec served at
`/openapi.json`. A canonical copy is committed to this repository as
[`openapi.json`](https://github.com/ichbinmeraj/fairino-fws/blob/master/openapi.json), and CI fails if the running app's **surface**
drifts from it — the set of operations and their required inputs, which is
what a client actually depends on. (The comparison is semantic, not
byte-exact: FastAPI emits cosmetically different JSON on different dependency
sets, so a byte match would fail for reasons that are not contract changes.)
So the surface cannot change without a commit that a reviewer sees — there are
no silent surface changes, even before 1.0.

The WebSocket streams (`/ws/state`, `/ws/events`) are **not** in the OpenAPI
spec — FastAPI does not describe WebSockets — so they are documented separately
in [`WEBSOCKETS.md`](https://github.com/ichbinmeraj/fairino-fws/blob/master/WEBSOCKETS.md) and their shape is pinned by tests.

## The pre-1.0 promise

While the version is `0.x`:

- **Additive changes happen without notice.** A new route, a new optional
  request field, a new field in a response — these cannot break a client that
  was not using them, so they arrive in any release.
- **Breaking changes are allowed, but never silent.** Removing a route,
  removing an operation, adding a required parameter or body field, changing a
  type — any of these will be:
  1. called out in [`CHANGELOG.md`](https://github.com/ichbinmeraj/fairino-fws/blob/master/CHANGELOG.md) under the release, and
  2. flagged by CI, which runs `tools/check_contract.py --since <last tag>`
     and classifies every change as additive or breaking.

  A breaking change is a decision someone made and wrote down, not something
  you discover when your integration starts returning 422.

At 1.0 the second rule tightens: breaking changes will require a major version
bump. The mechanism is already in place; only the promise changes.

## What "breaking" means here

Defined from the client's side — a change that can make a request that worked
yesterday fail today:

| Change | Classification |
|---|---|
| Route or operation removed | **breaking** |
| New required query parameter | **breaking** |
| New required request-body field | **breaking** |
| Type of a field changed | **breaking** |
| New route or operation | additive |
| New optional field | additive |
| A required field made optional | additive |

## Checking a change yourself

```bash
# Did I change the surface? (regenerates and diffs the committed spec)
python tools/check_contract.py

# I changed it on purpose — record the new contract:
python tools/check_contract.py --write     # then commit openapi.json

# Is my pending change breaking, versus the last release?
python tools/check_contract.py --since v0.1.0a13
```

## Pinning

Pin the alpha you tested against, since any alpha may carry a breaking change:

```
fairino-fws==0.1.0a13        # exact, while pre-1.0
```

Once 1.0 ships, a compatible-release pin (`fairino-fws~=1.0`) will be safe,
because breaking changes will move the major version.
