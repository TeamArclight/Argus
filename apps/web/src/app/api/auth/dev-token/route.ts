import { NextResponse } from 'next/server';
import crypto from 'crypto';
/**
 * Local development sign-in helper.
 *
 * Hardened per audit findings C-1 and C-2. The endpoint is CLOSED unless every
 * one of the following holds:
 *
 *   1. ARGUS_ENABLE_DEV_AUTH === 'true'   (explicit, server-side opt-in)
 *   2. APP_ENV  !== 'production'
 *   3. NODE_ENV !== 'production'
 *   4. ARGUS_DEV_AUTH_EMAIL and ARGUS_DEV_AUTH_PASSWORD are both configured
 *   5. ARGUS_JWT_SECRET is configured and at least 32 characters
 *
 * There are no built-in credentials: with nothing configured the route fails
 * closed rather than accepting a value published in this repository. The signing
 * secret is read server-side only and never returned or logged.
 *
 * This remains a development shortcut. Issuing production credentials belongs in
 * the API service behind a real user store — see audit finding C-2, deferred.
 */

// node:crypto and Buffer are required for HMAC signing and constant-time compare.
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';


export const SUPPORTED_ROLES = [
  'PROCUREMENT_OFFICER',
  'REVIEWER',
  'AUDITOR',
  'ADMIN',
] as const;

export type SupportedRole = (typeof SUPPORTED_ROLES)[number];
/** Roles mintable without any further opt-in. ADMIN is deliberately excluded. */
const DEFAULT_ALLOWED_ROLES: readonly SupportedRole[] = [
  'PROCUREMENT_OFFICER',
  'REVIEWER',
  'AUDITOR',
];

const DEFAULT_ROLE: SupportedRole = 'PROCUREMENT_OFFICER';


const ROLE_PROFILES: Record<
  SupportedRole,
  { sub: string; name: string; email: string }
> = {
  PROCUREMENT_OFFICER: {
    sub: 'argus-local-demo-officer',
    name: 'Demo Procurement Officer',
    email: 'demo.procurement@argus.local',
  },
  REVIEWER: {
    sub: 'argus-local-demo-reviewer',
    name: 'Demo Bid Reviewer',
    email: 'demo.reviewer@argus.local',
  },
  AUDITOR: {
    sub: 'argus-local-demo-auditor',
    name: 'Demo Procurement Auditor',
    email: 'demo.auditor@argus.local',
  },
  ADMIN: {
    sub: 'argus-local-demo-admin',
    name: 'Demo ARGUS Administrator',
    email: 'demo.admin@argus.local',
  },
};

const NO_STORE_HEADERS = { 'Cache-Control': 'no-store' } as const;

function isTruthyFlag(value: string | undefined): boolean {
  return typeof value === 'string' && ['true', '1', 'yes'].includes(value.trim().toLowerCase());
}

/** Constant-time string comparison that does not leak length through early exit. */
function safeEquals(a: string, b: string): boolean {
  const bufA = Buffer.from(a, 'utf8');
  const bufB = Buffer.from(b, 'utf8');
  if (bufA.length !== bufB.length) {
    // Still burn a comparison so the timing profile does not depend on length.
    crypto.timingSafeEqual(bufA, bufA);
    return false;
  }
  return crypto.timingSafeEqual(bufA, bufB);
}

function notFound(): NextResponse {
  return new NextResponse('Not Found', { status: 404, headers: NO_STORE_HEADERS });
}

function json(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, { status, headers: NO_STORE_HEADERS });
}

function resolveAllowedRoles(): SupportedRole[] {
  const configured = (process.env.ARGUS_DEV_AUTH_ALLOWED_ROLES || '')
    .split(',')
    .map((r) => r.trim().toUpperCase())
    .filter((r): r is SupportedRole => (SUPPORTED_ROLES as readonly string[]).includes(r));

  const allowed = configured.length > 0 ? configured : [...DEFAULT_ALLOWED_ROLES];

  // ADMIN is never mintable from an allowlist alone; it needs its own opt-in.
  const withoutAdmin = allowed.filter((r) => r !== 'ADMIN');
  if (isTruthyFlag(process.env.ARGUS_DEV_AUTH_ALLOW_ADMIN)) {
    return [...withoutAdmin, 'ADMIN'];
  }
  return withoutAdmin;
}

export async function POST(request: Request) {
  // ---- Gate 1: explicit opt-in, closed by default -------------------------
  if (!isTruthyFlag(process.env.ARGUS_ENABLE_DEV_AUTH)) {
    return notFound();
  }

  // ---- Gate 2/3: never in a production environment ------------------------
  if (
    (process.env.APP_ENV || '').trim().toLowerCase() === 'production' ||
    process.env.NODE_ENV === 'production'
  ) {
    return notFound();
  }

  // ---- Gate 4: credentials must be supplied by the operator ---------------
  const expectedEmail = (process.env.ARGUS_DEV_AUTH_EMAIL || '').trim();
  const expectedPassword = process.env.ARGUS_DEV_AUTH_PASSWORD || '';

  if (!expectedEmail || !expectedPassword) {
    return json(
      {
        error:
          'Development sign-in is enabled but unconfigured. Set ARGUS_DEV_AUTH_EMAIL and ARGUS_DEV_AUTH_PASSWORD in the server environment.',
      },
      503
    );
  }

  // ---- Gate 5: signing material -------------------------------------------
  const secret = process.env.ARGUS_JWT_SECRET;
  if (!secret || secret.trim().length < 32) {
    return json(
      { error: 'ARGUS_JWT_SECRET is missing or shorter than 32 characters.' },
      503
    );
  }

  let body: { email?: unknown; password?: unknown; role?: unknown } = {};
  try {
    body = await request.json();
  } catch {
    // Body is empty or not JSON; treated as missing credentials below.
  }

  const email = typeof body.email === 'string' ? body.email.trim() : '';
  const password = typeof body.password === 'string' ? body.password : '';

  const emailOk = email.length > 0 && safeEquals(email, expectedEmail);
  const passwordOk = password.length > 0 && safeEquals(password, expectedPassword);

  if (!emailOk || !passwordOk) {
    return json({ error: 'Invalid email or password' }, 401);
  }

  // ---- Role selection: allowlist, never arbitrary client input ------------
  const allowedRoles = resolveAllowedRoles();
  if (allowedRoles.length === 0) {
    return json(
      { error: 'No development roles are permitted by ARGUS_DEV_AUTH_ALLOWED_ROLES.' },
      503
    );
  }

  const rawRole = typeof body.role === 'string' ? body.role.trim().toUpperCase() : '';
  const requestedRole = rawRole.length > 0 ? rawRole : DEFAULT_ROLE;

  if (!allowedRoles.includes(requestedRole as SupportedRole)) {
    return json(
      {
        error: `Development role '${requestedRole}' is not permitted. Allowed roles: ${allowedRoles.join(', ')}`,
      },
      403
    );
  }

  const role = requestedRole as SupportedRole;
  const profile = ROLE_PROFILES[role];

  const now = Math.floor(Date.now() / 1000);
  const expirySeconds = 3600; // 60 minutes, matching the API default

  const header = { alg: 'HS256', typ: 'JWT' };

  const payload = {
    sub: profile.sub,
    role,
    name: profile.name,
    email: profile.email,
    iss: process.env.ARGUS_JWT_ISSUER || 'argus-api',
    aud: process.env.ARGUS_JWT_AUDIENCE || 'argus-clients',
    iat: now,
    exp: now + expirySeconds,
  };

  const base64UrlEncode = (obj: object): string =>
    Buffer.from(JSON.stringify(obj)).toString('base64url');

  const unsignedToken = `${base64UrlEncode(header)}.${base64UrlEncode(payload)}`;
  const signature = crypto
    .createHmac('sha256', secret)
    .update(unsignedToken)
    .digest('base64url');

  return json(
    {
      token: `${unsignedToken}.${signature}`,
      principal: {
        user_id: payload.sub,
        name: payload.name,
        role: payload.role,
        email: payload.email,
      },
      expires_in: expirySeconds,
    },
    200
  );
}

/** POST only. Every other verb is rejected explicitly rather than by framework default. */
function methodNotAllowed(): NextResponse {
  return new NextResponse('Method Not Allowed', {
    status: 405,
    headers: { ...NO_STORE_HEADERS, Allow: 'POST' },
  });
}

export const GET = methodNotAllowed;
export const PUT = methodNotAllowed;
export const PATCH = methodNotAllowed;
export const DELETE = methodNotAllowed;
export const HEAD = methodNotAllowed;
