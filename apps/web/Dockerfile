# ARGUS web app (Next.js 14) — Phase 9
FROM node:24-alpine AS deps
WORKDIR /srv/web
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-alpine AS build
WORKDIR /srv/web
COPY --from=deps /srv/web/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* vars are inlined at build time; pass via --build-arg in CI.
RUN npm run build

FROM node:24-alpine AS run
WORKDIR /srv/web
ENV NODE_ENV=production
COPY --from=build /srv/web/node_modules ./node_modules
COPY --from=build /srv/web/.next ./.next
COPY --from=build /srv/web/public ./public
COPY package.json next.config.js ./
EXPOSE 3000
CMD ["npm", "start"]
