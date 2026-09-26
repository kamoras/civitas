import { pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Feedback",
  description: "Report a data error, an accessibility barrier, or suggest a feature for Civitas.",
  path: "/feedback",
});

export default function FeedbackLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
