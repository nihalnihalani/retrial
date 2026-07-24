interface Props {
  braintrustUrl: string | null;
  braintrustLabel: string;
  prUrl: string | null;
  prLabel: string;
}

// The governance receipts. Real links only — a tile renders only when its URL
// exists (no fake placeholders): the Braintrust permalink from the run, and the
// PR tile only once a pr_opened event has actually landed.
export function ReceiptTiles({ braintrustUrl, braintrustLabel, prUrl, prLabel }: Props) {
  if (!braintrustUrl && !prUrl) return null;

  return (
    <div className="winner-receipts">
      {braintrustUrl && (
        <a className="receipt braintrust" href={braintrustUrl} target="_blank" rel="noreferrer">
          <span className="receipt-icon">◈</span>
          <span className="receipt-body">
            <span className="receipt-title">{braintrustLabel}</span>
            <span className="receipt-sub mono">{prettyUrl(braintrustUrl)}</span>
          </span>
        </a>
      )}
      {prUrl && (
        <a className="receipt pr" href={prUrl} target="_blank" rel="noreferrer">
          <span className="receipt-icon">⑃</span>
          <span className="receipt-body">
            <span className="receipt-title">{prLabel}</span>
            <span className="receipt-sub mono">{prettyUrl(prUrl)}</span>
          </span>
        </a>
      )}
    </div>
  );
}

// Strip protocol and decode for a compact, readable link label.
function prettyUrl(url: string): string {
  try {
    const u = new URL(url);
    return decodeURIComponent(u.host + u.pathname);
  } catch {
    return url;
  }
}
