# Hotel Booking Workflow deterministic fixtures

This repository contains a deterministic seed package for local development and
automated tests.

## Seed local/test data

```bash
python -m hbw_seed.deterministic ./tmp/hbw_seed.sqlite3
```

The seed command resets the SQLite database at the provided path and recreates a
known Hotel Booking Workflow scenario set covering search, availability,
booking, payment, cancellation, refund, review moderation, and admin audit flows.

Fixture documentation and expected outcomes are in
[`docs/deterministic-seed-fixtures.md`](docs/deterministic-seed-fixtures.md).

## Validate

```bash
python run_tests.py
```

If `pytest` is installed, the same tests can also be run with `python -m pytest`.
