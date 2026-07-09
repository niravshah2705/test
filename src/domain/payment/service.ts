import type { ServiceResult } from "@/domain/shared";
import { createPaymentRepository, type PaymentRepository } from "./repository";

export type PaymentService = {
  health(): Promise<ServiceResult<"ready">>;
};

export function createPaymentService(repository: PaymentRepository = createPaymentRepository()): PaymentService {
  return {
    async health() {
      await repository.ping();
      return { ok: true, value: "ready" };
    },
  };
}
