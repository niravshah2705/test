import { getDatabaseClient } from "@/lib/db";

export type AvailabilityRepository = {
  ping(): Promise<void>;
};

export function createAvailabilityRepository(): AvailabilityRepository {
  const db = getDatabaseClient();

  return {
    async ping() {
      void db;
    },
  };
}
