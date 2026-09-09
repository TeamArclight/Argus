import { NextResponse } from 'next/server';
import crypto from 'crypto';

export const SUPPORTED_ROLES = [
  'PROCUREMENT_OFFICER',
  'REVIEWER',
  'AUDITOR',
  'ADMIN',
] as const;

export type SupportedRole = (typeof SUPPORTED_ROLES)[number];

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

/**
 * Development-Only Temporary Token Issuance Endpoint.
 * Strictly disabled in production environments.
 * Uses exact backend claims and signing configuration from ARGUS API.
 * Never exposes the signing secret to the client.
 */
export async function POST(request: Request) {
  // Strictly forbid in production environments
  if (process.env.APP_ENV === 'production' || (process.env.NODE_ENV === 'production' && process.env.APP_ENV !== 'development')) {
    return new NextResponse('Not Found', { status: 404 });
  }

  // Parse credentials from request body
  let body: { email?: unknown; password?: unknown; role?: unknown } = {};
  try {
    body = await request.json();
  } catch {
    // Body is empty or not JSON
  }

  const expectedEmail = (process.env.ARGUS_DEV_AUTH_EMAIL || 'demo.procurement@argus.local').trim();
  const expectedPassword = process.env.ARGUS_DEV_AUTH_PASSWORD || 'ArgusDemo2026!';

  const email = typeof body.email === 'string' ? body.email.trim() : '';
  const password = typeof body.password === 'string' ? body.password : '';

  if (!email || !password || email !== expectedEmail || password !== expectedPassword) {
    return NextResponse.json(
      { error: 'Invalid email or password' },
      { status: 401 }
    );
  }

  // Validate role against allowlist
  const rawRole = typeof body.role === 'string' ? body.role.trim() : '';
  const requestedRole = rawRole.length > 0 ? rawRole : 'PROCUREMENT_OFFICER';

  if (!SUPPORTED_ROLES.includes(requestedRole as SupportedRole)) {
    return NextResponse.json(
      {
        error: `Unsupported development role: '${requestedRole}'. Allowed roles: ${SUPPORTED_ROLES.join(', ')}`,
      },
      { status: 400 }
    );
  }

  const role = requestedRole as SupportedRole;
  const profile = ROLE_PROFILES[role];

  const secret = process.env.ARGUS_JWT_SECRET;
  if (!secret || secret.trim().length < 32) {
    return NextResponse.json(
      {
        error: 'ARGUS_JWT_SECRET is missing or under 32 characters in development environment.',
      },
      { status: 500 }
    );
  }

  const now = Math.floor(Date.now() / 1000);
  const expirySeconds = 3600; // 60 minutes

  const header = {
    alg: 'HS256',
    typ: 'JWT',
  };

  const payload = {
    sub: profile.sub,
    role: role,
    name: profile.name,
    email: profile.email,
    iss: 'argus-api',
    aud: 'argus-clients',
    iat: now,
    exp: now + expirySeconds,
  };

  const base64UrlEncode = (obj: object): string =>
    Buffer.from(JSON.stringify(obj)).toString('base64url');

  const encodedHeader = base64UrlEncode(header);
  const encodedPayload = base64UrlEncode(payload);
  const unsignedToken = `${encodedHeader}.${encodedPayload}`;

  const signature = crypto
    .createHmac('sha256', secret)
    .update(unsignedToken)
    .digest('base64url');

  const token = `${unsignedToken}.${signature}`;

  return NextResponse.json({
    token,
    principal: {
      user_id: payload.sub,
      name: payload.name,
      role: payload.role,
      email: payload.email,
    },
    expires_in: expirySeconds,
  });
}
