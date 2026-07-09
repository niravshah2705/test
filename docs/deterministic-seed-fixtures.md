# Deterministic seed fixtures

NIR-510 adds a self-contained SQLite seed module at `hbw_seed.deterministic`.
It resets the target database and recreates the same fixture set on every run.
All identifiers, emails, URLs, payment provider references, and dates are test
values; no production secrets or real payment credentials are embedded.

## Running the seed

```bash
python -m hbw_seed.deterministic ./tmp/hbw_seed.sqlite3
```

The command drops and recreates the fixture tables, inserts deterministic rows,
and prints table counts. It is safe to rerun after clearing local or test data.
Use `reset_and_seed(":memory:")` from tests for an in-memory database.

## Fixed availability window

The primary search and booking verification window is:

- Check-in: `2031-06-10`
- Check-out: `2031-06-12`

A single-night maintenance scenario also covers `2031-06-10` to `2031-06-11`.

## Core records

- Users: `admin@example.test` (`admin`) and `guest@example.test` (`guest`).
- Cities: San Francisco and New York.
- Hotels:
  - `bay-view-grand` / Bay View Grand, San Francisco.
  - `mission-garden-inn` / Mission Garden Inn, San Francisco.
  - `central-loft-hotel` / Central Loft Hotel, New York.
- Amenities: Wi-Fi, Breakfast, Pool, Parking, Spa, Fitness Center.
- Each hotel has two room types; each room type has two physical rooms.
- Hotel and room images use `https://fixtures.example.test/...` URLs.
- Reviews include published and unpublished rows.
- Payments use provider `fixture_gateway` and references prefixed with `fx_`.

## Fixture scenarios and expected outcomes

| Scenario | Fixture IDs | Expected result for `2031-06-10` to `2031-06-12` |
| --- | --- | --- |
| Available room type | `rt_garden_family` at `mission-garden-inn` | One Family Studio remains available; one room is held by pending payment. |
| Partially available hotel | `bay-view-grand` | Hotel is searchable, but inventory is constrained by sold-out Deluxe King, one confirmed suite reservation, and one suite maintenance block. |
| Sold-out room type | `rt_bay_king`, reservations `res_bay_king_guest_confirmed` and `res_bay_king_auth_confirmed` | Deluxe King has zero available rooms because both physical rooms are confirmed. |
| Hotel-level closure | `blk_loft_hotel_closed` on `central-loft-hotel` | All Central Loft Hotel room types are unavailable despite the hotel being searchable in New York. |
| Room-type closure | `blk_garden_queen_closed` on `rt_garden_queen` | Garden Queen is unavailable; Family Studio at the same hotel can still be booked if inventory remains. |
| Room-level maintenance block | `blk_bay_suite_maint` on `room_bay_suite_602` | The blocked suite cannot be booked for the overlapping night; with the other suite confirmed, Executive Suite availability is zero for the two-night window. |
| Guest-checkout confirmed reservation | `res_bay_king_guest_confirmed` | Counts against availability and has no authenticated `user_id`. |
| Authenticated confirmed reservation | `res_bay_king_auth_confirmed`, `res_bay_suite_confirmed` | Counts against availability and is linked to `usr_guest`. |
| Pending-payment reservation | `res_garden_family_pending` | Holds one Family Studio room until payment completion or expiration; one sibling room remains available. |
| Cancelled reservation | `res_bay_suite_cancelled` plus `ref_bay_suite_cancelled` | Does not consume inventory; refund record documents the cancellation payment outcome. |
| Expired reservation | `res_garden_queen_expired` | Does not consume inventory; included for hold-expiration flows. |
| Published/unpublished review moderation | `rev_bay_pub`, `rev_bay_unpub` | Public review queries should include published rows and exclude unpublished rows. |
| Admin audit trail | `aud_seed_run`, `aud_hotel_closure`, `aud_room_type_closure`, `aud_refund` | Admin/back-office flows can verify deterministic audit records for seed, block, and refund actions. |

## Automated booking test scenarios

NIR-521 adds framework-neutral booking domain helpers and automated tests that use
these fixtures without external services. The tests intentionally keep payment
provider behavior mocked with `fixture_gateway` references and create temporary
SQLite databases per case.

- Utility coverage validates stay date parsing, total calculation, money payloads,
  and occupancy/capacity decisions.
- Availability integration coverage verifies overlapping reservations, hotel and
  room blocks, back-to-back reservations, expired holds, and last-room inventory.
- Reservation transaction coverage creates pending holds with `BEGIN IMMEDIATE`,
  treats repeated reservation IDs as duplicate creation requests, and returns a
  `409` conflict when the final room has already been held.
- Payment lifecycle coverage records successful captures, voided failed payments,
  payment amount mismatches, and duplicate webhook idempotency.
- Cancellation coverage updates reservation state, refunds captured payments, and
  releases room inventory.
- Authorization and API contract coverage verifies shared success/error envelopes,
  validation failures, conflicts, forbidden access, not-found responses, and
  success payloads.
- End-to-end coverage exercises guest search, room selection, guest details,
  mocked payment, confirmation lookup, cross-user access denial, and eligible
  cancellation.

## Audit trail policy

NIR-523 adds a structured `audit_records` schema with `actor_user_id`,
`actor_type`, `event_type`, `entity_type`, `entity_id`, safe JSON `metadata`, and
`created_at`. Runtime audit calls cover reservation creation/confirmation/
cancellation, payment intent creation, payment success/failure, refund creation,
hotel/room-type/room admin updates, and availability-block creation/deletion.
Webhook payment events use `actor_type='webhook'`; background jobs can use
`actor_type='system'` when no normal user exists.

Audit metadata intentionally excludes card numbers, CVC/CVV, provider secrets,
raw provider payloads, and full request payloads. Duplicate provider webhook
references are idempotent: the payment result is returned as a duplicate and no
second audit event is written for the already-processed provider event.

Audit write failure policy prioritizes booking correctness. Reservation,
payment, refund, cancellation, webhook, and background audit writes are
best-effort and should not block the user/system action. Admin inventory
mutations block on audit write failure because the audit record is part of the
back-office change contract.

## Availability query expectation

A room is available when it is active, overlaps no hotel/room-type/room-level
availability block, and has no overlapping `confirmed` or `pending_payment`
reservation. `cancelled` and `expired` reservations intentionally do not consume
inventory.
