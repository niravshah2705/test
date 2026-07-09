import type { ServiceResult } from "@/domain/shared";
import { createReservationRepository, type ReservationRepository } from "./repository";

export type ReservationService = {
  health(): Promise<ServiceResult<"ready">>;
};

export function createReservationService(repository: ReservationRepository = createReservationRepository()): ReservationService {
  return {
    async health() {
      await repository.ping();
      return { ok: true, value: "ready" };
    },
  };
}
