import { getDatabaseClient } from "@/lib/db";

export type AuditRepository = {
  ping(): Promise<void>;
};

export function createAuditRepository(): AuditRepository {
  const db = getDatabaseClient();

  return {
    async ping() {
      void db;
    },
  };
}
