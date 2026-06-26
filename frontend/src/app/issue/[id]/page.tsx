import { Metadata } from "next";
import { redirect } from "next/navigation";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const SITE = "https://civitas.paramain.com";

async function fetchIssue(id: string) {
  try {
    const res = await fetch(`${BACKEND}/api/action/issues/${id}`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const issue = await fetchIssue(id);

  const title = issue?.title
    ? `${issue.title} — Civitas`
    : "Civitas Action Center";
  const description =
    issue?.summary?.slice(0, 200) ??
    "Track what Congress is doing — and what you can do about it.";

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      url: `${SITE}/issue/${id}`,
      siteName: "Civitas",
      images: [
        {
          url: `${SITE}/api/og?issue=${id}`,
          width: 1200,
          height: 630,
          alt: issue?.title ?? "Civitas Action Center",
        },
      ],
      type: "article",
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [`${SITE}/api/og?issue=${id}`],
    },
  };
}

export default async function IssuePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  redirect(`/action?issue=${id}`);
}
