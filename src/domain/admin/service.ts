import type { ServiceResult } from "@/domain/shared";
import { createAdminRepository, type AdminRepository } from "./repository";

export type AdminService = {
  health(): Promise<ServiceResult<"ready">>;
};

export function createAdminService(repository: AdminRepository = createAdminRepository()): AdminService {
  return {
    async health() {
      await repository.ping();
      return { ok: true, value: "ready" };
    },
  };
}
