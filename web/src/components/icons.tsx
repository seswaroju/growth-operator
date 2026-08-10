// Drawn icon set — one consistent stroke weight, no emoji (craft floor: emoji standing in for an
// icon system is a tell). Stroke icons inherit `currentColor`; size via className (default 18px).

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { className?: string };

function Stroke({ className = "h-[18px] w-[18px]", children, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

// Faceted gem — the brand monogram (filled, uses currentColor).
export function Mark({ className = "h-5 w-5" }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className} aria-hidden="true">
      <path d="M12 3l3.4 4.2H8.6L12 3zM7.8 8.4h8.4L12 21 7.8 8.4z" />
    </svg>
  );
}

export const Bell = (p: IconProps) => (
  <Stroke {...p}>
    <path d="M6 8a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6z" />
    <path d="M10 20a2 2 0 0 0 4 0" />
  </Stroke>
);

export const Check = (p: IconProps) => (
  <Stroke {...p}>
    <path d="M20 6L9 17l-5-5" />
  </Stroke>
);

export const CheckCircle = (p: IconProps) => (
  <Stroke {...p}>
    <path d="M4 12a8 8 0 1 0 16 0 8 8 0 0 0-16 0z" />
    <path d="M9 12l2 2 4-4" />
  </Stroke>
);

export const ArrowRight = (p: IconProps) => (
  <Stroke {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Stroke>
);

export const SignOut = (p: IconProps) => (
  <Stroke {...p}>
    <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" />
    <path d="M10 17l-5-5 5-5M4 12h11" />
  </Stroke>
);

export const Ticket = (p: IconProps) => (
  <Stroke {...p}>
    <path d="M4 8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2 2 2 0 0 0 0 4 2 2 0 0 1-2 2H6a2 2 0 0 1-2-2 2 2 0 0 0 0-4z" />
    <path d="M14 6v12" />
  </Stroke>
);

export const Gear = (p: IconProps) => (
  <Stroke {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1L7 17M17 7l2.1-2.1" />
  </Stroke>
);

export const MessageCircle = (p: IconProps) => (
  <Stroke {...p}>
    <path d="M21 11.5a8.5 8.5 0 0 1-12.3 7.6L3 21l1.9-5.7A8.5 8.5 0 1 1 21 11.5z" />
  </Stroke>
);

export const Grid = (p: IconProps) => (
  <Stroke {...p}>
    <rect x="4" y="4" width="7" height="7" rx="1.5" />
    <rect x="13" y="4" width="7" height="7" rx="1.5" />
    <rect x="4" y="13" width="7" height="7" rx="1.5" />
    <rect x="13" y="13" width="7" height="7" rx="1.5" />
  </Stroke>
);
