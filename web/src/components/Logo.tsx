/**
 * The Panel mark and its Steam variant (approved identity — see design/README.md).
 *
 * Colors bind to the design tokens, so both marks re-skin with the theme.
 * Steam is contextual only: splash/empty states and while the agent is thinking.
 */
export function Logo({ steam = false, className }: { steam?: boolean; className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} role="img" aria-label="GameGusto">
      <g
        fill="none"
        stroke="var(--gg-ink)"
        strokeWidth="4.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <rect x="8" y="44" width="48" height="10" rx="5" />
        <path d="M22 44V28" />
      </g>
      {steam && (
        <path
          d="M15 8c-2-2 2-3 0-6M22 7c-2-2 2-3 0-6M29 8c-2-2 2-3 0-6"
          fill="none"
          stroke="var(--gg-warmth)"
          strokeWidth="2.8"
          strokeLinecap="round"
        />
      )}
      <circle cx="22" cy="19" r="8.5" fill="var(--gg-thrill)" />
      <circle cx="25.5" cy="16" r="2" fill="#fff" opacity="0.85" />
      <circle cx="40" cy="39" r="5" fill="var(--gg-charge)" />
      <circle cx="50" cy="39" r="5" fill="var(--gg-charge)" />
    </svg>
  );
}
