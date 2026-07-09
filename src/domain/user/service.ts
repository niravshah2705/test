import type { ServiceResult } from "@/domain/shared";
import { createUserRepository, type UserRepository } from "./repository";

export type UserService = {
  health(): Promise<ServiceResult<"ready">>;
};

export function createUserService(repository: UserRepository = createUserRepository()): UserService {
  return {
    async health() {
      await repository.ping();
      return { ok: true, value: "ready" };
    },
  };
}
