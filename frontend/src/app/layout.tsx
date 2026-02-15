import type { Metadata } from "next";
import { VT323, Press_Start_2P, Share_Tech_Mono } from "next/font/google";
import "./globals.css";

const vt323 = VT323({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-vt323",
});

const pressStart = Press_Start_2P({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-press-start",
});

const shareTech = Share_Tech_Mono({
  weight: "400",
  subsets: ["latin"],
  variable: "--font-share-tech",
});

export const metadata: Metadata = {
  title: "MODERN PUNK // EXPOSE THE MACHINE",
  description:
    "They don't want you to see this. Track corporate money in politics, expose corrupt senators, and follow the receipts. Your democracy is sponsored.",
  keywords: [
    "political corruption",
    "senator corruption",
    "corporate lobbying",
    "campaign finance",
    "money in politics",
  ],
  openGraph: {
    title: "MODERN PUNK // EXPOSE THE MACHINE",
    description: "Track corporate money in politics. Expose corrupt senators. Follow the receipts.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${vt323.variable} ${pressStart.variable} ${shareTech.variable} font-terminal antialiased`}
      >
        <div className="crt-overlay" aria-hidden="true" />
        {children}
      </body>
    </html>
  );
}
