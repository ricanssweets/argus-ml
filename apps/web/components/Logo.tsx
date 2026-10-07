// Brand mark: bronze "A" with a forward swoosh, on charcoal.
export function LogoMark({ size = 36 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      aria-label="Argus logo"
    >
      <defs>
        <linearGradient id="argus-bronze" x1="0" y1="0" x2="48" y2="48">
          <stop offset="0%" stopColor="#e8c9a0" />
          <stop offset="45%" stopColor="#c99763" />
          <stop offset="100%" stopColor="#a56e40" />
        </linearGradient>
      </defs>
      {/* Left leg */}
      <path d="M24 6 L8 42 H15 L24 18 L33 42 H40 Z" fill="url(#argus-bronze)" />
      {/* Crossbar swoosh */}
      <path
        d="M4 36 C 16 34, 30 28, 44 20 C 32 30, 18 36, 6 40 Z"
        fill="url(#argus-bronze)"
        opacity="0.95"
      />
    </svg>
  );
}

export function LogoLockup({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-ink-950 ring-1 ring-bronze-600/40">
        <LogoMark size={28} />
      </div>
      {!compact && (
        <div>
          <p className="text-lg font-bold tracking-[0.22em] text-stone-100">
            ARGUS
          </p>
          <p className="text-[9px] uppercase tracking-[0.28em] text-bronze-400">
            Data drives clarity
          </p>
        </div>
      )}
    </div>
  );
}
