import { pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Admin",
  description: "Civitas administration.",
  path: "/admin",
  noindex: true,
});

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
