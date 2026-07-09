import { getDatabaseClient } from "@/lib/db";
import type { HotelSearchCriteria, HotelSummary } from "./types";

export type HotelRepository = {
  search(criteria: HotelSearchCriteria): Promise<HotelSummary[]>;
};

export function createHotelRepository(): HotelRepository {
  const db = getDatabaseClient();

  return {
    async search(criteria) {
      void db;
      void criteria;
      return [];
    },
  };
}
