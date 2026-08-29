# Frontend image — Next.js production build.
#
# Important subtlety: NEXT_PUBLIC_* environment variables are baked into the JavaScript
# bundle at BUILD time, not read at container start time like a typical backend env var.
# This means NEXT_PUBLIC_API_URL must be passed as a Docker build argument, not just set
# in the container's runtime environment — setting it only at `docker run` time would have
# no effect, since the build already happened. This is a common, easy-to-miss Next.js
# deployment gotcha, not specific to this project.

FROM node:20-slim AS builder

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./

# Baked in at build time — see the note above. Passed via --build-arg when building this
# image, pointing at wherever the backend is actually reachable in production (NOT
# localhost:8000, which only makes sense for local dev).
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json

EXPOSE 3000

CMD ["npm", "start"]
