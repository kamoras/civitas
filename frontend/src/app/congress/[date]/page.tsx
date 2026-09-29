import { Metadata } from "next";
import { notFound } from "next/navigation";
import { pageMetadata } from "@/lib/site";
import { ISO_DATE, fetchDay } from "@/lib/congressServer";
import { longDate } from "@/lib/congress";
import DayReportView from "@/components/congress/DayReportView";

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
      path: `/congress/${date}`,
      noindex: true,
    });
  const report = await fetchDay(date);
  return pageMetadata({
    title: `Congress on ${longDate(date)}`,
    description: report?.sentence ?? `What the Senate and the House did on ${longDate(date)}.`,
    path: `/congress/${date}`,
    type: "article",
  });
}

export default async function CongressDayPage({ params }: { params: Promise<{ date: string }> }) {
  const { date } = await params;
  if (!ISO_DATE.test(date)) notFound();
  const report = await fetchDay(date);
  if (!report) notFound();
  return <DayReportView report={report} />;
}
