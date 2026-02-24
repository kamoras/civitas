export default function Marquee({ items }: { items: string[] }) {
  const text = items.join(" /// ");
  const doubled = `${text} /// ${text} /// `;

  return (
    <div
      className="w-full overflow-hidden border-y border-matrix-green/30 bg-crt-black/80 py-3"
      role="marquee"
      aria-label="Scrolling site information"
    >
      {/* Accessible version for screen readers */}
      <div className="sr-only">
        <ul>
          {items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      </div>
      {/* Visual scrolling version */}
      <div aria-hidden="true" className="animate-marquee whitespace-nowrap font-terminal text-lg text-matrix-green/70">
        {doubled}
      </div>
    </div>
  );
}
