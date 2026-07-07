-- Room and inventory schema for the online hotel booking domain.
-- Target dialect: PostgreSQL 14+

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE bed_type AS ENUM ('single', 'twin', 'double', 'queen', 'king', 'sofa_bed', 'bunk_bed');
CREATE TYPE inventory_hold_status AS ENUM ('active', 'confirmed', 'expired', 'released');

CREATE TABLE hotels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE room_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hotel_id UUID NOT NULL REFERENCES hotels(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    base_occupancy INTEGER NOT NULL,
    max_occupancy INTEGER NOT NULL,
    max_adults INTEGER NOT NULL,
    max_children INTEGER NOT NULL DEFAULT 0,
    total_rooms INTEGER NOT NULL,
    size_square_meters NUMERIC(7, 2),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT room_types_hotel_code_unique UNIQUE (hotel_id, code),
    CONSTRAINT room_types_base_occupancy_positive CHECK (base_occupancy > 0),
    CONSTRAINT room_types_max_occupancy_positive CHECK (max_occupancy > 0),
    CONSTRAINT room_types_max_adults_positive CHECK (max_adults > 0),
    CONSTRAINT room_types_max_children_nonnegative CHECK (max_children >= 0),
    CONSTRAINT room_types_total_rooms_nonnegative CHECK (total_rooms >= 0),
    CONSTRAINT room_types_capacity_order CHECK (base_occupancy <= max_occupancy),
    CONSTRAINT room_types_guest_capacity CHECK (max_adults + max_children >= max_occupancy),
    CONSTRAINT room_types_size_positive CHECK (size_square_meters IS NULL OR size_square_meters > 0)
);

CREATE TABLE room_type_beds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_type_id UUID NOT NULL REFERENCES room_types(id) ON DELETE CASCADE,
    bed_type bed_type NOT NULL,
    quantity INTEGER NOT NULL,
    sleeps INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT room_type_beds_quantity_positive CHECK (quantity > 0),
    CONSTRAINT room_type_beds_sleeps_positive CHECK (sleeps > 0)
);

CREATE TABLE room_amenities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE room_type_amenities (
    room_type_id UUID NOT NULL REFERENCES room_types(id) ON DELETE CASCADE,
    amenity_id UUID NOT NULL REFERENCES room_amenities(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_type_id, amenity_id)
);

CREATE TABLE room_type_inventory (
    room_type_id UUID NOT NULL REFERENCES room_types(id) ON DELETE CASCADE,
    inventory_date DATE NOT NULL,
    total_inventory INTEGER NOT NULL,
    available_inventory INTEGER NOT NULL,
    held_inventory INTEGER NOT NULL DEFAULT 0,
    sold_inventory INTEGER NOT NULL DEFAULT 0,
    closed_to_arrival BOOLEAN NOT NULL DEFAULT false,
    closed_to_departure BOOLEAN NOT NULL DEFAULT false,
    min_length_of_stay INTEGER,
    max_length_of_stay INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_type_id, inventory_date),
    CONSTRAINT room_type_inventory_total_nonnegative CHECK (total_inventory >= 0),
    CONSTRAINT room_type_inventory_available_nonnegative CHECK (available_inventory >= 0),
    CONSTRAINT room_type_inventory_held_nonnegative CHECK (held_inventory >= 0),
    CONSTRAINT room_type_inventory_sold_nonnegative CHECK (sold_inventory >= 0),
    CONSTRAINT room_type_inventory_not_oversold CHECK (available_inventory + held_inventory + sold_inventory <= total_inventory),
    CONSTRAINT room_type_inventory_min_los_positive CHECK (min_length_of_stay IS NULL OR min_length_of_stay > 0),
    CONSTRAINT room_type_inventory_max_los_positive CHECK (max_length_of_stay IS NULL OR max_length_of_stay > 0),
    CONSTRAINT room_type_inventory_los_order CHECK (min_length_of_stay IS NULL OR max_length_of_stay IS NULL OR min_length_of_stay <= max_length_of_stay)
);

CREATE TABLE inventory_holds (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_type_id UUID NOT NULL REFERENCES room_types(id) ON DELETE CASCADE,
    checkout_token TEXT NOT NULL UNIQUE,
    status inventory_hold_status NOT NULL DEFAULT 'active',
    quantity INTEGER NOT NULL,
    check_in_date DATE NOT NULL,
    check_out_date DATE NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inventory_holds_quantity_positive CHECK (quantity > 0),
    CONSTRAINT inventory_holds_date_order CHECK (check_in_date < check_out_date),
    CONSTRAINT inventory_holds_confirmed_status CHECK ((status = 'confirmed') = (confirmed_at IS NOT NULL)),
    CONSTRAINT inventory_holds_released_status CHECK ((status IN ('expired', 'released')) = (released_at IS NOT NULL))
);

CREATE TABLE inventory_hold_dates (
    inventory_hold_id UUID NOT NULL REFERENCES inventory_holds(id) ON DELETE CASCADE,
    room_type_id UUID NOT NULL,
    inventory_date DATE NOT NULL,
    quantity INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (inventory_hold_id, inventory_date),
    CONSTRAINT inventory_hold_dates_quantity_positive CHECK (quantity > 0),
    FOREIGN KEY (room_type_id, inventory_date)
        REFERENCES room_type_inventory(room_type_id, inventory_date)
        ON DELETE CASCADE
);

CREATE INDEX idx_room_types_hotel_active
    ON room_types (hotel_id, is_active);

CREATE INDEX idx_room_type_beds_room_type
    ON room_type_beds (room_type_id, bed_type);

CREATE INDEX idx_room_amenities_category_name
    ON room_amenities (category, name);

CREATE INDEX idx_room_type_inventory_date_available
    ON room_type_inventory (inventory_date, room_type_id)
    WHERE available_inventory > 0;

CREATE INDEX idx_inventory_holds_active_expiry
    ON inventory_holds (expires_at)
    WHERE status = 'active';

CREATE INDEX idx_inventory_holds_room_type_dates
    ON inventory_holds (room_type_id, check_in_date, check_out_date);

CREATE INDEX idx_inventory_hold_dates_inventory_lookup
    ON inventory_hold_dates (room_type_id, inventory_date);
