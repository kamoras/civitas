import { Metadata } from "next";
import { notFound } from "next/navigation";
import { pageMetadata } from "@/lib/site";
import { ISO_MONTH, fetchMonth } from "@/lib/congressServer";
import { monthLabel } from "@/lib/congress";
import PeriodReportView from "@/components/congress/PeriodReportView";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ month: string }>;
}): Promise<Metadata> {
  const { month } = await params;
  if (!ISO_MONTH.test(month))
    return pageMetadata({
      title: "Not found",
      description: "",
      path: `/congress/month/${month}`,
      noindex: true,
    });
  const report = await fetchMonth(month);
  // No record for this date: the page is a 404, and says so to crawlers.
  if (!report)
    return pageMetadata({
      title: "Not found",
      description: "",
      path: `/congress/month/${month}`,
      noindex: true,
    });
  return pageMetadata({
    title: `Congress in ${monthLabel(month)}`,
    description: report?.sentence ?? `What the Senate and the House did in ${monthLabel(month)}.`,
    path: `/congress/month/${month}`,
  });
}

export default async function CongressMonthPage({
  params,
}: {
  params: Promise<{ month: string }>;
}) {
  const { month } = await params;
  if (!ISO_MONTH.test(month)) notFound();
  const report = await fetchMonth(month);
  if (!report) notFound();
  return <PeriodReportView report={report} kind="month" />;
}
