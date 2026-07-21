/**
 * The Panel mark (approved identity — see design/README.md).
 *
 * One mark, used everywhere. Colors bind to the design tokens, so it re-skins
 * with the theme.
 */
export function Logo({ className }: { className?: string }) {
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
      <circle cx="22" cy="19" r="8.5" fill="var(--gg-thrill)" />
      <circle cx="25.5" cy="16" r="2" fill="#fff" opacity="0.85" />
      <circle cx="40" cy="39" r="5" fill="var(--gg-charge)" />
      <circle cx="50" cy="39" r="5" fill="var(--gg-charge)" />
    </svg>
  );
}
