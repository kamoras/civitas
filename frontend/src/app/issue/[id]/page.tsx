import { Metadata } from "next";
import Link from "next/link";
import { ActionIssue } from "@/types/action";

const BACKEND = process.env.BACKEND_URL || "http://backend:8000";
const SITE = "https://civitas-research.org";

async function fetchIssue(id: string): Promise<ActionIssue | null> {
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

function SourceBadge({ name, url }: { name: string; url?: string }) {
  const base =
    "text-[10px] px-2 py-0.5 border border-matrix-green/30 text-matrix-green/60 hover:text-matrix-green hover:border-matrix-green/60 transition-colors";
  if (url) {
    return (
      <a href={url} target="_blank" rel="noopener noreferrer" className={base}>
        {name}
      </a>
    );
  }
  return <span className={base}>{name}</span>;
}

export default async function IssuePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const issue = await fetchIssue(id);

  if (!issue) {
    return (
      <main className="min-h-screen bg-black text-matrix-green font-mono flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="text-2xl">ISSUE NOT FOUND</div>
          <Link href="/action" className="text-sm text-matrix-green/60 hover:text-matrix-green underline">
            ← Back to Action Center
          </Link>
        </div>
      </main>
    );
  }

  const paragraphs = issue.fullStory
    ? issue.fullStory.split(/\n\n+/).filter((p) => p.trim())
    : null;

  return (
    <main className="min-h-screen bg-black text-matrix-green font-mono">
      <div className="max-w-3xl mx-auto px-4 py-10">
        {/* Nav */}
        <div className="mb-8">
          <Link
            href="/action"
            className="text-xs text-matrix-green/50 hover:text-matrix-green transition-colors"
          >
            ← ACTION CENTER
          </Link>
        </div>

        {/* Header */}
        <header className="mb-8 space-y-3 border-b border-matrix-green/20 pb-8">
          {issue.policyAreas?.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {issue.policyAreas.map((area) => (
                <span
                  key={area}
                  className="text-[10px] px-2 py-0.5 border border-matrix-green/30 text-matrix-green/50"
                >
                  {area}
                </span>
              ))}
            </div>
          )}
          <h1 className="text-xl leading-tight text-matrix-green">
            {issue.title}
          </h1>
          <p className="text-sm text-matrix-green/70 leading-relaxed">
            {issue.summary}
          </p>
          <div className="text-[10px] text-matrix-green/30">
            {new Date(issue.date + "T12:00:00").toLocaleDateString("en-US", {
              year: "numeric",
              month: "long",
              day: "numeric",
            })}
          </div>
        </header>

        {/* Full story */}
        {paragraphs ? (
          <article className="space-y-5 text-sm text-matrix-green/80 leading-relaxed mb-12">
            {paragraphs.map((para, i) => {
              // Detect markdown-style headers (# or ## Heading)
              if (para.startsWith("# ") || para.startsWith("## ")) {
                return (
                  <h2
                    key={i}
                    className="text-base text-matrix-green font-bold mt-8 first:mt-0 border-l-2 border-matrix-green/40 pl-3"
                  >
                    {para.replace(/^#+\s+/, "")}
                  </h2>
                );
              }
              // Bold paragraph: starts and ends with **
              if (para.startsWith("**") && para.endsWith("**") && para.length > 4) {
                const stripped = para.slice(2, -2);
                return (
                  <p key={i} className="text-matrix-green font-medium">{stripped}</p>
                );
              }
              return (
                <p key={i}>{para}</p>
              );
            })}
          </article>
        ) : (
          <div className="mb-12 py-10 border border-matrix-green/20 text-center text-matrix-green/40 text-sm">
            Full story not yet available. Check back soon.
          </div>
        )}

        {/* Key facts */}
        {issue.facts?.length > 0 && (
          <section className="mb-10">
            <h2 className="text-xs text-matrix-green/40 mb-4 tracking-widest">
              KEY FACTS
            </h2>
            <ul className="space-y-3">
              {issue.facts.map((fact, i) => (
                <li
                  key={i}
                  className="flex gap-3 text-sm text-matrix-green/70"
                >
                  <span className="text-matrix-green/30 shrink-0 mt-0.5">▸</span>
                  <span>{fact}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* Sources */}
        {issue.sourceNames?.length > 0 && (
          <section className="mb-10">
            <h2 className="text-xs text-matrix-green/40 mb-4 tracking-widest">
              SOURCES
            </h2>
            <div className="flex flex-wrap gap-2">
              {issue.sourceNames.map((name, i) => (
                <SourceBadge
                  key={name}
                  name={name}
                  url={issue.sourceUrls?.[i]}
                />
              ))}
            </div>
          </section>
        )}

        {/* Related bills */}
        {issue.relatedBills?.length > 0 && (
          <section className="mb-10">
            <h2 className="text-xs text-matrix-green/40 mb-4 tracking-widest">
              RELATED LEGISLATION
            </h2>
            <ul className="space-y-2">
              {issue.relatedBills.map((bill) => (
                <li key={bill.id}>
                  {bill.internalUrl ? (
                    <Link
                      href={bill.internalUrl}
                      className="text-sm text-matrix-green/70 hover:text-matrix-green transition-colors underline underline-offset-2"
                    >
                      {bill.name}
                    </Link>
                  ) : (
                    <a
                      href={bill.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm text-matrix-green/70 hover:text-matrix-green transition-colors underline underline-offset-2"
                    >
                      {bill.name}
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}

        {/* Back */}
        <div className="pt-8 border-t border-matrix-green/10">
          <Link
            href="/action"
            className="text-xs text-matrix-green/50 hover:text-matrix-green transition-colors"
          >
            ← Back to Action Center
          </Link>
        </div>
      </div>
    </main>
  );
}
