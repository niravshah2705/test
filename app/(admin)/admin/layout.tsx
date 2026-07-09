import type { ReactNode } from "react";
import { requireAdmin } from "@/lib/auth";

export default async function AdminLayout({ children }: { children: ReactNode }) {
  await requireAdmin();

  return <section aria-label="Administration">{children}</section>;
}
