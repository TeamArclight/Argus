# ARGUS web tier — production build.
#
# The container MUST NOT run `next dev`. Dev mode leaves NODE_ENV=development,
# which is one of the conditions the /api/auth/dev-token guard checks, and it
# ships an unoptimised bundle. See audit findings C-1 and L-11.
# ---------------------------------------------------------------------------
# 1. Dependencies (from the committed lockfile, same as CI)
# ---------------------------------------------------------------------------
FROM node:20-alpine AS deps

WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

# ---------------------------------------------------------------------------
# 2. Build
#
# NEXT_PUBLIC_* values are inlined into the browser bundle at build time, so the
# public API origin must be supplied as a build argument. The default is a
# local-development convenience only; a remote deployment must override it or
# every visitor's browser will call its own machine (audit finding H-8).
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# 3. Runtime (non-root, production mode)
# ---------------------------------------------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup -g 10001 -S appgroup \
    && adduser -u 10001 -S appuser -G appgroup

COPY --from=builder --chown=appuser:appgroup /app ./

USER appuser

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/landing.html').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
