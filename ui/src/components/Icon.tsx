// Bundled line icons (24px grid, drawn with currentColor). No icon font, no network.

const PATHS = {
  runs: "M4 6h16M4 12h16M4 18h10M18 16l3 2-3 2z",
  cases: "M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h6",
  envs: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.5 2.7 3.5 5.7 3.5 9s-1 6.3-3.5 9c-2.5-2.7-3.5-5.7-3.5-9s1-6.3 3.5-9z",
  models: "M8 8h8v8H8zM4 10h4M4 14h4M16 10h4M16 14h4M10 4v4M14 4v4M10 16v4M14 16v4",
  play: "M8 5l11 7-11 7z",
  rerun: "M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5",
  stop: "M7 7h10v10H7z",
  plus: "M12 5v14M5 12h14",
  check: "M5 12.5l4.5 4.5L19 7",
  cross: "M6 6l12 12M18 6L6 18",
  alert: "M12 4l9 16H3zM12 10v4M12 17v.5",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z",
  auto: "M12 3a9 9 0 1 0 0 18zM12 3a9 9 0 0 1 0 18",
  link: "M10 14l10-10M14 4h6v6M18 14v6H4V6h6",
  image: "M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M15 9.5a1 1 0 1 0 0-.01",
  save: "M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  trash: "M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13",
  dot: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  chevron: "M9 6l6 6-6 6",
  key: "M15 4a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM11.5 12.5L4 20M7 17l2 2M5 19l1.5 1.5",
  pause: "M8 5v14M16 5v14",
  step: "M6 5l9 7-9 7zM18 5v14",
  skip: "M5 5l7 7-7 7M13 5l7 7-7 7",
  hand: "M8 13V5.5a1.5 1.5 0 0 1 3 0V11M11 10.5V4.5a1.5 1.5 0 0 1 3 0V11M14 10.5V6a1.5 1.5 0 0 1 3 0v7c0 4-2.5 7-6 7-2.6 0-4.2-1.4-5.6-3.6L4 14.2a1.5 1.5 0 0 1 2.4-1.8L8 14",
  copy: "M9 9h10v11H9zM5 15V4h10",
  eye: "M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12zM12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
  shield: "M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6zM9 12l2 2 4-4",
  cloud: "M7 18.5a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 17.8 8.6 5 5 0 0 1 17 18.5z",
  compass: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM15.5 8.5l-2 5-5 2 2-5z",
  plug: "M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, label }: { name: IconName; size?: number; label?: string }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
