import type { ServiceResult } from "@/domain/shared";
import { createAvailabilityRepository, type AvailabilityRepository } from "./repository";

export type AvailabilityService = {
  health(): Promise<ServiceResult<"ready">>;
};

export function createAvailabilityService(repository: AvailabilityRepository = createAvailabilityRepository()): AvailabilityService {
  return {
    async health() {
      await repository.ping();
      return { ok: true, value: "ready" };
    },
  };
}
