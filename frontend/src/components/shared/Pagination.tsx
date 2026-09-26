/** PREV / page n of N / NEXT control for the paginated scorecard sections
 * (stock trades, holdings). Renders nothing for a single page. */
export default function Pagination({
  page,
  totalPages,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  onPageChange: (p: number) => void;
}) {
  if (totalPages <= 1) return null;
  return (
    <div className="flex items-center justify-center gap-2 mt-4">
      <button
        onClick={() => onPageChange(page - 1)}
        disabled={page === 1}
        aria-label="Previous page"
        className="text-xs px-2 py-1 font-mono text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed"
      >
        &lt; PREV
      </button>
      <span className="text-xs text-ink-min">
        page {page}/{totalPages}
      </span>
      <button
        onClick={() => onPageChange(page + 1)}
        disabled={page === totalPages}
        aria-label="Next page"
        className="text-xs px-2 py-1 font-mono text-ink-lo hover:text-phos disabled:text-ink-min disabled:cursor-not-allowed"
      >
        NEXT &gt;
      </button>
    </div>
  );
}
