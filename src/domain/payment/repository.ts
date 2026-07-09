import { getDatabaseClient } from "@/lib/db";

export type PaymentRepository = {
  ping(): Promise<void>;
};

export function createPaymentRepository(): PaymentRepository {
  const db = getDatabaseClient();

  return {
    async ping() {
      void db;
    },
  };
}
