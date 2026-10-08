import { Metadata } from "next";
import { notFound } from "next/navigation";
import { absoluteUrl, pageMetadata } from "@/lib/site";
import { ISO_DATE, fetchWeek } from "@/lib/congressServer";
import { longDate } from "@/lib/congress";
import PeriodReportView from "@/components/congress/PeriodReportView";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ date: string }>;
}): Promise<Metadata> {
  const { date } = await params;
  if (!ISO_DATE.test(date))
    return pageMetadata({
      title: "Not found",
      description: "",
      path: `/congress/week/${date}`,
      noindex: true,
    });
  const report = await fetchWeek(date);
  // No record for this date: the page is a 404, and says so to crawlers.
  if (!report)
    return pageMetadata({
      title: "Not found",
      description: "",
      path: `/congress/week/${date}`,
      noindex: true,
    });
  const start = report.start;
  const title = `Congress, week of ${longDate(start).replace(/^\w+, /, "")}`;
  return pageMetadata({
    title,
    description: report?.sentence ?? "What the Senate and the House did this week.",
    // Canonical is the week's Monday: any day of the week serves the same page.
    path: `/congress/week/${start}`,
    images: [
      { url: absoluteUrl(`/api/og?congressWeek=${start}`), width: 1200, height: 630, alt: title },
    ],
  });
}

export default async function CongressWeekPage({ params }: { params: Promise<{ date: string }> }) {
  const { date } = await params;
  if (!ISO_DATE.test(date)) notFound();
  const report = await fetchWeek(date);
  if (!report) notFound();
  return <PeriodReportView report={report} kind="week" />;
}
