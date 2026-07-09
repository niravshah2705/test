import type { Identifier } from "@/domain/shared";

export type HotelSummary = {
  id: Identifier;
  name: string;
  city: string;
};

export type HotelSearchCriteria = {
  city?: string;
  checkIn?: string;
  checkOut?: string;
  guests?: number;
};
