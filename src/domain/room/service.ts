import type { ServiceResult } from "@/domain/shared";
import { createRoomRepository, type RoomRepository } from "./repository";

export type RoomService = {
  health(): Promise<ServiceResult<"ready">>;
};

export function createRoomService(repository: RoomRepository = createRoomRepository()): RoomService {
  return {
    async health() {
      await repository.ping();
      return { ok: true, value: "ready" };
    },
  };
}
