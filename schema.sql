-- Relational schema for hotel rooms, guests, and bookings.
-- Target dialect: PostgreSQL 14+
--
-- Key integrity guarantees:
-- - rooms belong to hotels through a required FK
-- - bookings belong to one room and one primary guest through required FKs
-- - booking_guests links every staying guest to a booking through required FKs
-- - no two active bookings can overlap for the same physical room/date range
-- - booking_audit_log records immutable booking lifecycle changes

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TYPE hotel_status AS ENUM ('draft', 'active', 'inactive');
CREATE TYPE room_status AS ENUM ('active', 'maintenance', 'inactive');
CREATE TYPE booking_status AS ENUM ('pending', 'confirmed', 'checked_in', 'checked_out', 'cancelled', 'no_show');
CREATE TYPE booking_audit_action AS ENUM ('created', 'confirmed', 'checked_in', 'checked_out', 'cancelled', 'guest_updated', 'room_changed', 'dates_changed', 'note_added');

CREATE TABLE hotels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    status hotel_status NOT NULL DEFAULT 'draft',
    phone TEXT,
    email TEXT,
    address_line1 TEXT NOT NULL,
    address_line2 TEXT,
    city TEXT NOT NULL,
    region TEXT NOT NULL,
    postal_code TEXT,
    country_code CHAR(2) NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT hotels_code_unique UNIQUE (code),
    CONSTRAINT hotels_email_format CHECK (email IS NULL OR email ~* '^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$'),
    CONSTRAINT hotels_country_code_uppercase CHECK (country_code = upper(country_code))
);

CREATE TABLE rooms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hotel_id UUID NOT NULL REFERENCES hotels(id) ON DELETE CASCADE,
    room_number TEXT NOT NULL,
    floor TEXT,
    room_type TEXT NOT NULL,
    status room_status NOT NULL DEFAULT 'active',
    max_occupancy INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT rooms_hotel_room_number_unique UNIQUE (hotel_id, room_number),
    CONSTRAINT rooms_max_occupancy_positive CHECK (max_occupancy > 0)
);

CREATE TABLE guests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_reference TEXT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    date_of_birth DATE,
    government_id_last4 CHAR(4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT guests_external_reference_unique UNIQUE (external_reference),
    CONSTRAINT guests_email_format CHECK (email IS NULL OR email ~* '^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$')
);

CREATE TABLE bookings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    confirmation_code TEXT NOT NULL,
    room_id UUID NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
    primary_guest_id UUID NOT NULL REFERENCES guests(id) ON DELETE RESTRICT,
    status booking_status NOT NULL DEFAULT 'pending',
    check_in_date DATE NOT NULL,
    check_out_date DATE NOT NULL,
    booked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_at TIMESTAMPTZ,
    cancellation_reason TEXT,
    guest_count INTEGER NOT NULL DEFAULT 1,
    special_requests TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT bookings_confirmation_code_unique UNIQUE (confirmation_code),
    CONSTRAINT bookings_date_order CHECK (check_in_date < check_out_date),
    CONSTRAINT bookings_guest_count_positive CHECK (guest_count > 0),
    CONSTRAINT bookings_cancelled_timestamp CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL)),
    CONSTRAINT bookings_active_room_date_no_overlap EXCLUDE USING gist (
        room_id WITH =,
        daterange(check_in_date, check_out_date, '[)') WITH &&
    ) WHERE (status IN ('confirmed', 'checked_in'))
);

CREATE TABLE booking_guests (
    booking_id UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    guest_id UUID NOT NULL REFERENCES guests(id) ON DELETE RESTRICT,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (booking_id, guest_id)
);

CREATE UNIQUE INDEX booking_guests_one_primary_per_booking
    ON booking_guests (booking_id)
    WHERE is_primary;

CREATE TABLE booking_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    action booking_audit_action NOT NULL,
    actor_id TEXT,
    actor_type TEXT NOT NULL DEFAULT 'system',
    previous_status booking_status,
    new_status booking_status,
    changed_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT booking_audit_log_changed_fields_object CHECK (jsonb_typeof(changed_fields) = 'object')
);

CREATE INDEX idx_hotels_status_location
    ON hotels (status, country_code, region, city);

CREATE INDEX idx_rooms_hotel_status_type
    ON rooms (hotel_id, status, room_type);

CREATE INDEX idx_guests_name
    ON guests (last_name, first_name);

CREATE INDEX idx_bookings_primary_guest
    ON bookings (primary_guest_id, check_in_date DESC);

CREATE INDEX idx_bookings_room_dates
    ON bookings (room_id, check_in_date, check_out_date);

CREATE INDEX idx_bookings_active_arrivals
    ON bookings (check_in_date, room_id)
    WHERE status IN ('confirmed', 'checked_in');

CREATE INDEX idx_booking_audit_log_booking_created
    ON booking_audit_log (booking_id, created_at DESC);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER hotels_set_updated_at
    BEFORE UPDATE ON hotels
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER rooms_set_updated_at
    BEFORE UPDATE ON rooms
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER guests_set_updated_at
    BEFORE UPDATE ON guests
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER bookings_set_updated_at
    BEFORE UPDATE ON bookings
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
