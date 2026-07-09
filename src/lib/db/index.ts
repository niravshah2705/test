import "server-only";

export type DatabaseClient = {
  readonly boundary: "server-only";
};

export function getDatabaseClient(): DatabaseClient {
  return { boundary: "server-only" };
}
