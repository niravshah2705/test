import type { HotelSearchCriteria } from "./types";

export function parseHotelSearchCriteria(input: Partial<HotelSearchCriteria>): HotelSearchCriteria {
  return {
    city: input.city?.trim() || undefined,
    checkIn: input.checkIn,
    checkOut: input.checkOut,
    guests: input.guests,
  };
}
