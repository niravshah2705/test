# Hotel booking relational schema

This repository contains a PostgreSQL relational schema and ERD for hotels, physical rooms, guests, bookings, and booking audit history.

## Entity relationship diagram

```mermaid
erDiagram
    HOTELS ||--o{ ROOMS : contains
    ROOMS ||--o{ BOOKINGS : reserves
    GUESTS ||--o{ BOOKINGS : primary_guest
    BOOKINGS ||--o{ BOOKING_GUESTS : includes
    GUESTS ||--o{ BOOKING_GUESTS : stays_on
    BOOKINGS ||--o{ BOOKING_AUDIT_LOG : records

    HOTELS {
        uuid id PK
        text code UK
        text name
        hotel_status status
        text phone
        text email
        text address_line1
        text address_line2
        text city
        text region
        text postal_code
        char country_code
        text timezone
        timestamptz created_at
        timestamptz updated_at
    }

    ROOMS {
        uuid id PK
        uuid hotel_id FK
        text room_number
        text floor
        text room_type
        room_status status
        integer max_occupancy
        timestamptz created_at
        timestamptz updated_at
    }

    GUESTS {
        uuid id PK
        text external_reference UK
        text first_name
        text last_name
        text email
        text phone
        date date_of_birth
        char government_id_last4
        timestamptz created_at
        timestamptz updated_at
    }

    BOOKINGS {
        uuid id PK
        text confirmation_code UK
        uuid room_id FK
        uuid primary_guest_id FK
        booking_status status
        date check_in_date
        date check_out_date
        timestamptz booked_at
        timestamptz cancelled_at
        text cancellation_reason
        integer guest_count
        text special_requests
        timestamptz created_at
        timestamptz updated_at
    }

    BOOKING_GUESTS {
        uuid booking_id PK,FK
        uuid guest_id PK,FK
        boolean is_primary
        timestamptz added_at
    }

    BOOKING_AUDIT_LOG {
        uuid id PK
        uuid booking_id FK
        booking_audit_action action
        text actor_id
        text actor_type
        booking_status previous_status
        booking_status new_status
        jsonb changed_fields
        text note
        timestamptz created_at
    }
```

## Constraint summary

- `hotels.code` is unique; rooms are scoped to hotels with `rooms.hotel_id -> hotels.id`.
- `(hotel_id, room_number)` is unique so each physical room is identified once per hotel.
- `bookings.room_id -> rooms.id` and `bookings.primary_guest_id -> guests.id` are required foreign keys.
- `booking_guests` is a required join table for all staying guests with FKs to `bookings` and `guests`.
- `booking_guests_one_primary_per_booking` allows only one primary guest marker per booking.
- `bookings_confirmation_code_unique` guarantees globally unique confirmation codes.
- `bookings_date_order` enforces `check_in_date < check_out_date`.
- `bookings_active_room_date_no_overlap` is a PostgreSQL GiST exclusion constraint over `daterange(check_in_date, check_out_date, '[)')`; it prevents overlapping `confirmed` or `checked_in` bookings for the same physical room while allowing cancelled/no-show/pending rows to remain in history.
- `booking_audit_log` is append-only by convention and captures lifecycle action, actor, previous/new status, JSON field-level changes, notes, and timestamp.
- `created_at`/`updated_at` columns exist on mutable tables, with triggers maintaining `updated_at`.

See [`schema.sql`](./schema.sql) for the executable PostgreSQL DDL.
