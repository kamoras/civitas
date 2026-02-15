export default function Marquee({ items }: { items: string[] }) {
  const text = items.join(" /// ");
  // Duplicate for seamless loop
  const doubled = `${text} /// ${text} /// `;

  return (
    <div className="w-full overflow-hidden border-y border-matrix-green/30 bg-crt-black/80 py-3">
      <div className="animate-marquee whitespace-nowrap font-terminal text-lg text-matrix-green/70">
        {doubled}
      </div>
    </div>
  );
}
