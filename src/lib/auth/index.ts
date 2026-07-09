import "server-only";

export type SessionUser = {
  id: string;
  role: "guest" | "admin";
};

export async function getCurrentUser(): Promise<SessionUser | null> {
  return null;
}

export async function requireAdmin(): Promise<SessionUser> {
  const user = await getCurrentUser();
  if (!user || user.role !== "admin") {
    throw new Error("Admin access required");
  }
  return user;
}
