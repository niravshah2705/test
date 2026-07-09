import type { ServiceResult } from "@/domain/shared";
import { createAuditRepository, type AuditRepository } from "./repository";

export type AuditService = {
  health(): Promise<ServiceResult<"ready">>;
};

export function createAuditService(repository: AuditRepository = createAuditRepository()): AuditService {
  return {
    async health() {
      await repository.ping();
      return { ok: true, value: "ready" };
    },
  };
}
