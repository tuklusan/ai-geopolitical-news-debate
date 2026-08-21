# Changelog

All notable changes to this project. Versions follow [semantic versioning](https://semver.org).

## 1.1.0 — 2026-08-21

### Added

- `live-news-wall --init` writes a starter `config/.env` from a template now
  shipped inside the package. Setup no longer depends on shell-specific
  commands or on having a checkout to copy an example file from.
- `LOG_LEVEL` is a real, validated setting.
- A `--version` flag, and documentation for it.

### Fixed

- **`LOG_LEVEL` set in `config/.env` was silently ignored.** It was read at
  import time, before the dotenv file was loaded, so the level stayed at
  `INFO` however the file was written.
- **A model returning HTTP 200 with no text reported itself healthy.** The
  wall showed "Model: ok" while producing nothing at all, which is what
  happens with reasoning models that return `content: null`. An empty
  response now marks the model unhealthy.
- **Adding a persona did nothing.** `persona_keys()` returned a hardcoded
  list rather than deriving from `PERSONAS`, so a new speaker was invisible
  to selection, to the speaker panel and to message labels — contradicting
  the documented promise that adding one is all it takes.
- The documented setup used a placeholder key the code did not recognise, so
  pasting it produced a stream of authentication failures instead of the
  clean "no key configured" mode.
- The release workflow read a `pyproject.toml` version key that no longer
  exists once the version became dynamic, and would have failed on every tag.
- The context-window and prior-point limits were defined twice, in
  `engine.py` and `llm_client.py`, with nothing keeping them equal.

### Changed

- The project is an installable package. Modules moved from the repository
  root into `live_news_wall/`, so installing no longer drops generically
  named modules such as `database.py` and `engine.py` into `site-packages`.
- The version has a single source of truth in `live_news_wall/__init__.py`.
- Documentation states no version in prose, no pinned download URL and no
  pinned wheel filename, so nothing goes stale on the next release.
- Tests grew from 68 to 224, including checks that keep the documentation
  and the code from drifting apart.

## 1.0.0 — 2026-08-21

First release: the corrected, hardened descendant of the application
ChatDev's agents generated from a single prompt. Topic advancement,
per-topic anti-repetition memory, weighted speaker selection, the verbatim
parody notice, client-capacity shedding, three-tier feed deduplication and
bounded growth, verified across every available GitHub-hosted runner.
