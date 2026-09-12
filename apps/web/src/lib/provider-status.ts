export type CanonicalProviderStatus =
  | 'LIVE_CONNECTED'
  | 'DEMO_SYNTHETIC'
  | 'CONFIGURED_UNVERIFIED'
  | 'UNAVAILABLE'
  | 'FAILED'
  | 'UNKNOWN';

export interface ProviderStatusDisplay {
  code: CanonicalProviderStatus;
  label: string;
  badgeClass: string;
  dotClass: string;
  iconName: 'check' | 'sparkles' | 'info' | 'x' | 'help';
  description: string;
}

/**
 * Deterministically resolves the canonical status and truthful visual representation
 * for external statutory and AI service providers.
 *
 * CRITICAL RULE: Non-live providers (e.g. DEMO, SYNTHETIC, DOCUMENT, UNVERIFIED)
 * must NEVER display a green check or pulsing green dot.
 */
export function resolveProviderStatus(
  input: {
    mode?: string | null;
    configured_mode?: string | null;
    operational_health?: string | null;
    configuration_status?: string | null;
    configured?: boolean | null;
    status?: string | null;
    details?: string | null;
    notes?: string | null;
  } | null | undefined,
  isDemoEnvironment?: boolean
): ProviderStatusDisplay {
  if (isDemoEnvironment) {
    return {
      code: 'DEMO_SYNTHETIC',
      label: 'DEMO / SYNTHETIC',
      badgeClass: 'bg-amber-500/10 text-amber-300 border border-amber-500/30',
      dotClass: 'bg-amber-400',
      iconName: 'sparkles',
      description: 'Synthetic demo provider active (Demo Preview)',
    };
  }

  if (!input) {
    return {
      code: 'UNKNOWN',
      label: 'UNKNOWN',
      badgeClass: 'bg-zinc-800 text-zinc-400 border border-zinc-700',
      dotClass: 'bg-zinc-500',
      iconName: 'help',
      description: 'Telemetry query unverified or offline',
    };
  }

  const mode = (input.configured_mode || input.mode || '').toUpperCase();
  const health = (input.operational_health || input.status || '').toUpperCase();
  const isConfigured = input.configured ?? (input.configuration_status === 'CONFIGURED');
  const details = input.details || input.notes || '';

  // 1. Explicit terminal failures
  if (['FAILED', 'DOWN', 'ERROR', 'DEGRADED'].includes(health)) {
    return {
      code: 'FAILED',
      label: 'FAILED',
      badgeClass: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
      dotClass: 'bg-rose-400',
      iconName: 'x',
      description: details || 'Service endpoint failed or unreachable',
    };
  }

  // 2. Unconfigured / Unavailable
  if (!isConfigured || health === 'UNCONFIGURED' || health === 'UNAVAILABLE') {
    return {
      code: 'UNAVAILABLE',
      label: 'UNAVAILABLE',
      badgeClass: 'bg-slate-800 text-slate-400 border-slate-700',
      dotClass: 'bg-slate-500',
      iconName: 'help',
      description: details || 'Provider credentials or configuration missing',
    };
  }

  // 3. Demo / Synthetic Mode
  if (mode === 'DEMO' || mode === 'SYNTHETIC' || health === 'DEMO' || health === 'SYNTHETIC') {
    return {
      code: 'DEMO_SYNTHETIC',
      label: 'DEMO / SYNTHETIC',
      badgeClass: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
      dotClass: 'bg-amber-400',
      iconName: 'sparkles',
      description: details || 'Deterministic synthetic mock adapter active (DEMO mode)',
    };
  }

  // 4. Document extraction / Portal cached / Unverified mode
  if (
    mode === 'DOCUMENT' ||
    mode === 'PORTAL_CACHED' ||
    (mode === 'LIVE' && (health === 'UNKNOWN' || health === 'CONFIGURED'))
  ) {
    return {
      code: 'CONFIGURED_UNVERIFIED',
      label: 'CONFIGURED / UNVERIFIED',
      badgeClass: 'bg-sky-500/10 text-sky-400 border-sky-500/20',
      dotClass: 'bg-sky-400',
      iconName: 'info',
      description: details || 'Configured for document or cached verification; external live portal unverified',
    };
  }

  // 5. Live Connected (Verified live)
  if (
    mode === 'LIVE' &&
    ['AVAILABLE', 'CONNECTED', 'OK', 'HEALTHY'].includes(health)
  ) {
    return {
      code: 'LIVE_CONNECTED',
      label: 'LIVE / CONNECTED',
      badgeClass: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
      dotClass: 'bg-emerald-400 animate-pulse',
      iconName: 'check',
      description: details || 'Live API gateway verified and operational',
    };
  }

  // 6. Default fallback
  return {
    code: 'UNKNOWN',
    label: health || 'UNKNOWN',
    badgeClass: 'bg-zinc-800 text-zinc-400 border-zinc-700',
    dotClass: 'bg-zinc-500',
    iconName: 'help',
    description: details || 'Telemetry query unverified',
  };
}
