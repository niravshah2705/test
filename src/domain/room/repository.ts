import { getDatabaseClient } from "@/lib/db";

export type RoomRepository = {
  ping(): Promise<void>;
};

export function createRoomRepository(): RoomRepository {
  const db = getDatabaseClient();

  return {
    async ping() {
      void db;
    },
  };
}
