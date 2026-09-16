# ============================================================================
# 📚 LEARNER GUIDE: Frontend Dockerfile — frontend/Dockerfile (Next.js)
# ============================================================================
# WHAT IS THIS DOCKERFILE?
# ------------------------
# Builds a Docker image for Next.js 16 frontend (the main UI).
# Uses MULTI-STAGE BUILD (3 stages: deps, builder, runner) for small, secure image.
#
# WHAT IS MULTI-STAGE BUILD?
# --------------------------
# Instead of one big image with all tools, we use multiple FROM stages:
#   1. deps: Install dependencies (npm ci)
#   2. builder: Build Next.js (npm run build)
#   3. runner: Final image (only built output + minimal Node, no source, no dev deps)
# This makes final image SMALL and SECURE (no source code, no build tools).
#
# WHAT DOES IT DO?
# ----------------
# 1. deps: Copy package.json, npm ci --only=production
# 2. builder: Copy package.json, npm ci (all deps), copy source, build (npm run build)
# 3. runner: Copy built output from builder, run as non-root user, expose 3000
#
# WHY MULTI-STAGE?
# ----------------
# - Smaller image (runner has only standalone output, not node_modules + source)
# - Faster deploy (less to push/pull)
# - More secure (no build tools, no source in final image)
# - Next.js standalone output is optimized for Docker
#
# DOCKER CONCEPTS:
# ----------------
# - FROM ... AS <name> (named stage)
# - COPY --from=<stage> (copy from previous stage)
# - ARG (build-time variable, for NEXT_PUBLIC_API_URL)
# - ENV (runtime variable)
# - RUN addgroup/adduser (create non-root user)
# - USER (run as non-root)
# - EXPOSE, CMD
#
# HOW IT CONNECTS:
# ----------------
# - Built by: docker-compose.yml (frontend service, build: context: ./frontend)
# - Used by: docker-compose.yml (frontend service, ports 3001:3000) and k8s (frontend.yaml)
# - Args: NEXT_PUBLIC_API_URL=/api (browser calls /api/* → Ingress → API)
#         API_PROXY_URL=http://api:8000 (Next.js server rewrites /api/* to API)
#
# FLOW:
# -----
# 1. docker build -t my-frontend ./frontend
# 2. Stage deps: Install prod deps
# 3. Stage builder: Install all deps, build Next.js (creates .next/standalone)
# 4. Stage runner: Copy standalone output, run as nextjs user on 3000
# 5. docker run -p 3001:3000 my-frontend
# 6. Verify: docker images | grep frontend
#            docker run --rm -p 3001:3000 my-frontend && curl http://localhost:3001
# ============================================================================

# Base stage: Node 20 Alpine (lightweight Node.js)
#   - `node:20-alpine` = Node 20 on Alpine (small, ~50MB)
#   - `AS base` = Name this stage "base" for reuse
FROM node:20-alpine AS base

# Stage 1: deps — Install dependencies only when needed
#   - This stage is cached unless package.json changes (fast rebuilds)
FROM base AS deps
WORKDIR /app  # Workdir /app

# Copy package files (for npm ci)
#   - `package.json` = Dependencies list
#   - `package-lock.json` = Locked versions (for reproducible installs)
COPY package.json package-lock.json ./

# Install production dependencies only (no dev deps)
#   - `npm ci` = Clean install (from lock file, faster, reproducible)
#   - `--only=production` = Only prod deps (no eslint, typescript, etc.)
RUN npm ci --only=production

# Stage 2: builder — Build Next.js
FROM base AS builder
WORKDIR /app

# Copy package files and install ALL dependencies (including dev)
COPY package.json package-lock.json ./
RUN npm ci  # All deps (need dev deps for build)

# Copy all source code
#   - `COPY . .` = Copy frontend source (src/, public/, next.config.ts, etc.)
#   - Respects frontend/.dockerignore (excludes node_modules, .next, etc.)
COPY . .

# Build arg: NEXT_PUBLIC_API_URL (where browser calls API)
#   - `ARG` = Build-time variable (baked into JS at build)
#   - Default `/api` = Same-origin (browser → /api/* → Next.js rewrite → API)
#   - For k8s, also `/api` (Ingress routes /api/* to API Service)
ARG NEXT_PUBLIC_API_URL=/api
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL  # Also set as ENV for build

# Build arg: API_PROXY_URL (where Next.js server proxies /api/*)
#   - Inside Docker Compose, API service resolves via http://api:8000 (Docker DNS)
#   - Inside k8s, via http://llama-agent-api-service:8000 (but baked at build, so use /api for browser)
#   - This is for Next.js rewrites (next.config.ts)
ARG API_PROXY_URL=http://api:8000
ENV API_PROXY_URL=$API_PROXY_URL

# Build Next.js (creates .next/standalone for Docker)
#   - `npm run build` = Next.js build (requires next.config.ts with output: 'standalone')
#   - Output: .next/standalone (minimal server), .next/static (assets), public/
RUN npm run build

# Stage 3: runner — Final production image (minimal, secure)
FROM base AS runner
WORKDIR /app

# Set production env
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1  # Disable Next.js telemetry (privacy)

# Create non-root user for security (don't run as root!)
#   - `addgroup --system --gid 1001 nodejs` = Create group nodejs (GID 1001)
#   - `adduser --system --uid 1001 nextjs` = Create user nextjs (UID 1001)
RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# Copy built output from builder stage
#   - `public` = Static assets (images, etc.)
#   - `.next/standalone` = Minimal Node server (only needed files)
#   - `.next/static` = Static JS/CSS
#   - `--chown=nextjs:nodejs` = Own files as nextjs user (for security)
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Run as non-root user (security best practice)
USER nextjs

# Expose Next.js port (3000)
EXPOSE 3000

# Env for Next.js server
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"  # Listen on all interfaces (for Docker)

# Default command: Run Next.js standalone server
#   - `node server.js` = Next.js standalone server (from .next/standalone)
CMD ["node", "server.js"]

# 💡 LEARNER TIP: Next.js Standalone Output
#   next.config.ts must have: output: 'standalone' (creates minimal server)
#   Without it, you'd need to copy entire node_modules (huge!).
#   Check: cat frontend/next.config.ts | grep standalone
#   Build: docker build -t test ./frontend && docker run -p 3001:3000 test
