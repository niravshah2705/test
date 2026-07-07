-- Core hotel/property schema for the online hotel booking domain.
-- Target dialect: PostgreSQL 14+

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE property_status AS ENUM ('draft', 'active', 'inactive');
CREATE TYPE property_media_type AS ENUM ('image');
CREATE TYPE property_media_role AS ENUM ('gallery', 'hero', 'thumbnail');

CREATE TABLE properties (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    status property_status NOT NULL DEFAULT 'draft',
    star_rating NUMERIC(2, 1),
    check_in_time TIME,
    check_out_time TIME,
    search_city TEXT NOT NULL,
    search_region TEXT NOT NULL,
    search_country_code CHAR(2) NOT NULL,
    latitude NUMERIC(9, 6),
    longitude NUMERIC(9, 6),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT properties_star_rating_range CHECK (star_rating IS NULL OR (star_rating >= 0 AND star_rating <= 5)),
    CONSTRAINT properties_latitude_range CHECK (latitude IS NULL OR (latitude >= -90 AND latitude <= 90)),
    CONSTRAINT properties_longitude_range CHECK (longitude IS NULL OR (longitude >= -180 AND longitude <= 180)),
    CONSTRAINT properties_location_pair CHECK ((latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL))
);

CREATE TABLE property_addresses (
    property_id UUID PRIMARY KEY REFERENCES properties(id) ON DELETE CASCADE,
    line1 TEXT NOT NULL,
    line2 TEXT,
    neighborhood TEXT,
    city TEXT NOT NULL,
    region TEXT NOT NULL,
    postal_code TEXT,
    country_code CHAR(2) NOT NULL,
    formatted_address TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE amenities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE property_amenities (
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    amenity_id UUID NOT NULL REFERENCES amenities(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (property_id, amenity_id)
);

CREATE TABLE property_media (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    media_type property_media_type NOT NULL DEFAULT 'image',
    role property_media_role NOT NULL DEFAULT 'gallery',
    url TEXT NOT NULL,
    alt_text TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    width_px INTEGER,
    height_px INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT property_media_sort_order_nonnegative CHECK (sort_order >= 0),
    CONSTRAINT property_media_width_positive CHECK (width_px IS NULL OR width_px > 0),
    CONSTRAINT property_media_height_positive CHECK (height_px IS NULL OR height_px > 0)
);

CREATE TABLE property_policies (
    property_id UUID PRIMARY KEY REFERENCES properties(id) ON DELETE CASCADE,
    cancellation_policy TEXT,
    pet_policy TEXT,
    child_policy TEXT,
    smoking_policy TEXT,
    extra_bed_policy TEXT,
    house_rules TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_properties_public_search
    ON properties (status, search_country_code, search_region, search_city)
    WHERE status = 'active';

CREATE INDEX idx_properties_location
    ON properties (latitude, longitude)
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL;

CREATE INDEX idx_property_addresses_city_region
    ON property_addresses (country_code, region, city);

CREATE INDEX idx_amenities_category_name
    ON amenities (category, name);

CREATE INDEX idx_property_media_property_sort
    ON property_media (property_id, role, sort_order);
