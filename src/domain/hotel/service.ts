import type { ServiceResult } from "@/domain/shared";
import { createHotelRepository, type HotelRepository } from "./repository";
import type { HotelSearchCriteria, HotelSummary } from "./types";

export type HotelService = {
  searchHotels(criteria: HotelSearchCriteria): Promise<ServiceResult<HotelSummary[]>>;
};

export function createHotelService(repository: HotelRepository = createHotelRepository()): HotelService {
  return {
    async searchHotels(criteria) {
      const hotels = await repository.search(criteria);
      return { ok: true, value: hotels };
    },
  };
}
