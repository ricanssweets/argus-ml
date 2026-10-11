// Brand mark: the real bronze "A" from the Argus brand board.
export function LogoMark({ size = 36 }: { size?: number }) {
  return (
    <img
      src="/brand/argus-mark.png"
      alt="Argus logo"
      width={size}
      height={size}
      className="rounded-lg object-cover"
    />
  );
}

export function LogoLockup({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <LogoMark size={40} />
      {!compact && (
        <div>
          <p className="font-display text-xl tracking-[0.24em] text-stone-100">
            ARGUS
          </p>
          <p className="text-[9px] uppercase tracking-[0.3em] text-bronze-400">
            Data drives clarity
          </p>
        </div>
      )}
    </div>
  );
}
