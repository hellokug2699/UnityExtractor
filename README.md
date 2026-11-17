# UnityExtractor

Evidence-based scanner and, later, general-purpose resource extraction toolkit for Unity games.

> Current status: **P0 foundation in progress**. The current CLI inspects package structure and Unity fingerprints; it does not yet enumerate or export Unity objects.

## Requirements

- Python 3.10+
- No third-party runtime dependencies for P0

## Install from source

```bash
python -m pip install -e .
```

## Usage

```bash
unity-extractor /path/to/game.apk
unity-extractor /path/to/unpacked/game --format json
python -m unity_extractor /path/to/resource.bundle --output scan-report.json
```

Inputs currently supported:
- directories;
- ZIP and APK archives (inspected without extracting their contents);
- standalone files, including candidate Unity bundles and serialized files.

## What P0 reports

- Unity runtime and player-data filename evidence;
- UnityFS / UnityWeb / UnityRaw bundle signatures;
- conservative SerializedFile header candidates;
- Addressables catalog filename candidates;
- StreamingAssets path candidates;
- Mono and IL2CPP runtime clues;
- conservative Cocos2d-x runtime fingerprinting for non-Unity identification;
- Unity version strings when found in inspected data;
- confidence, warnings, and per-input errors.

Cocos2d-x detection currently recognizes the `libcocos2dcpp.so` runtime filename; it is a narrow identification aid, not a general multi-engine parser. Conflicting strong Unity and Cocos2d-x fingerprints remain `unknown` rather than being guessed.

A missing clue is **not** proof that a feature is absent. Encrypted, obfuscated, custom-packed, or unusually structured games may not be recognized. Version and backend detection are heuristic and are reported as unknown when evidence is insufficient.

## Safety and limits

The scanner does not execute package code or extract archive paths. Archive inspection has entry-count, per-entry read, total-read, and compression-ratio limits. It skips oversized entries and records warnings.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

The test suite currently uses Python's standard library plus pytest as the test runner.
