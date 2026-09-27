/** PREV / NEXT control for the paginated scorecard sections — with
 * "page n/N" between them (stock trades, holdings), or with numbered page
 * buttons (`numbered`, the voting record). Renders nothing for one page. */
export default function Pagination({
  page,
  totalPages,
  onPageChange,
  disabled = false,
  numbered = false,
}: {
  page: number;
  totalPages: number;
  onPageChange: (p: number) => void;
  /** Every button off, e.g. while the list this pager describes is being replaced. */
  disabled?: boolean;
  numbered?: boolean;
}) {
  if (totalPages <= 1) return null;

  const pages: (number | "...")[] = [];
  if (numbered) {
    for (let i = 1; i <= totalPages; i++) {
      if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) {
        pages.push(i);
      } else if (pages[pages.length - 1] !== "...") {
        pages.push("...");
      }
    }
  }

  const step = "text-xs px-2 py-1 font-mono text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed";
  return (
    <div className={`flex items-center justify-center mt-4 ${numbered ? "gap-1" : "gap-2"}`}>
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={disabled || page === 1}
        aria-label="Previous page"
        className={step}
      >
        &lt; PREV
      </button>
      {numbered ? (
        pages.map((p, i) =>
          p === "..." ? (
            <span key={`dot-${i}`} className="text-ink-min text-xs px-1">
              ...
            </span>
          ) : (
            <button
              key={p}
              onClick={() => onPageChange(p)}
              disabled={disabled}
              aria-label={`Page ${p}`}
              aria-current={p === page ? "page" : undefined}
              className={`text-xs w-7 h-7 font-mono border transition-all disabled:cursor-not-allowed ${
                p === page
                  ? "text-ink-hi border-white/15 bg-white/[0.03]"
                  : "text-ink-min border-transparent hover:border-white/[0.07]"
              }`}
            >
              {p}
            </button>
          ),
        )
      ) : (
        <span className="text-xs text-ink-min">
          page {page}/{totalPages}
        </span>
      )}
      <button
        onClick={() => onPageChange(page + 1)}
        disabled={disabled || page === totalPages}
        aria-label="Next page"
        className={step}
      >
        NEXT &gt;
      </button>
    </div>
  );
}
