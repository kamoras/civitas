import { Metadata } from "next";
import { notFound } from "next/navigation";
import { pageMetadata } from "@/lib/site";
import { ISO_DATE, fetchWeek } from "@/lib/congressServer";
import { longDate } from "@/lib/congress";
import PeriodReportView from "@/components/congress/PeriodReportView";

export async function generateMetadata({ params }: { params: Promise<{ date: string }> }): Promise<Metadata> {
  const { date } = await params;
  if (!ISO_DATE.test(date)) return pageMetadata({ title: "Not found", description: "", path: `/congress/week/${date}`, noindex: true });
  const report = await fetchWeek(date);
  const start = report?.start ?? date;
  return pageMetadata({
    title: `Congress, week of ${longDate(start).replace(/^\w+, /, "")}`,
    description: report?.sentence ?? "What the Senate and the House did this week.",
    // Canonical is the week's Monday: any day of the week serves the same page.
    path: `/congress/week/${start}`,
  });
}

export default async function CongressWeekPage({ params }: { params: Promise<{ date: string }> }) {
  const { date } = await params;
  if (!ISO_DATE.test(date)) notFound();
  const report = await fetchWeek(date);
  if (!report) notFound();
  return <PeriodReportView report={report} kind="week" />;
}
