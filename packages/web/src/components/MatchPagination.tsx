import Link from "next/link";

interface MatchPaginationProps {
  page: number;
  total: number;
  limit: number;
  basePath?: string;
}

function pageHref(basePath: string, page: number): string {
  if (page <= 1) return basePath;
  return `${basePath}?page=${page}`;
}

export function MatchPagination({ page, total, limit, basePath = "/matches" }: MatchPaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / limit));
  if (totalPages <= 1) return null;

  const start = (page - 1) * limit + 1;
  const end = Math.min(page * limit, total);

  const pages: number[] = [];
  const windowStart = Math.max(1, page - 2);
  const windowEnd = Math.min(totalPages, page + 2);
  for (let p = windowStart; p <= windowEnd; p++) pages.push(p);

  return (
    <div className="pagination">
      <div className="pagination-info">
        Showing {start}–{end} of {total} matches
      </div>
      <div className="pagination-links">
        {page > 1 && (
          <Link href={pageHref(basePath, page - 1)} className="btn btn-ghost">
            ← Prev
          </Link>
        )}
        {pages.map((p) => (
          <Link
            key={p}
            href={pageHref(basePath, p)}
            className={`btn btn-ghost${p === page ? " is-active" : ""}`}
          >
            {p}
          </Link>
        ))}
        {page < totalPages && (
          <Link href={pageHref(basePath, page + 1)} className="btn btn-ghost">
            Next →
          </Link>
        )}
      </div>
    </div>
  );
}
