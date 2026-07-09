import { getDatabaseClient } from "@/lib/db";

export type UserRepository = {
  ping(): Promise<void>;
};

export function createUserRepository(): UserRepository {
  const db = getDatabaseClient();

  return {
    async ping() {
      void db;
    },
  };
}
