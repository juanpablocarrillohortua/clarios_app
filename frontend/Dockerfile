# =============================================================================
# Clarios Energy - reverse proxy with the web app
#
# Stage layout:
#   build - installs the locked dependencies and builds the static app
#   web   - Caddy with the build in /srv; carries no Node and no source
#
# The app is static files, so it needs no server of its own: the reverse proxy
# that is already the stack's single ingress serves it. The Caddyfile is not
# copied in; compose mounts it, as before.
# =============================================================================

ARG NODE_VERSION=24

# -----------------------------------------------------------------------------
FROM node:${NODE_VERSION}-bookworm-slim AS build
# -----------------------------------------------------------------------------
WORKDIR /app

# Dependency layer: cached until the lockfile itself changes. `npm ci` installs
# exactly what is locked, and .npmrc keeps install scripts off.
COPY package.json package-lock.json .npmrc ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY . .
RUN npm run build

# -----------------------------------------------------------------------------
FROM caddy:2 AS web
# -----------------------------------------------------------------------------
COPY --from=build /app/dist /srv
