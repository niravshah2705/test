import { getDatabaseClient } from "@/lib/db";

export type ReservationRepository = {
  ping(): Promise<void>;
};

export function createReservationRepository(): ReservationRepository {
  const db = getDatabaseClient();

  return {
    async ping() {
      void db;
    },
  };
}
