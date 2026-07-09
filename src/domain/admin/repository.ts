import { getDatabaseClient } from "@/lib/db";

export type AdminRepository = {
  ping(): Promise<void>;
};

export function createAdminRepository(): AdminRepository {
  const db = getDatabaseClient();

  return {
    async ping() {
      void db;
    },
  };
}
