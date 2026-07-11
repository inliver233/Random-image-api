# Go Random Engine — in-memory pick service (contracts/random-engine.openapi.yaml).
# Build from repo root or this directory:
#   docker build -f services/random-engine/Dockerfile -t random-engine services/random-engine
FROM golang:1.22-alpine AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download 2>/dev/null || true
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/random-engine ./cmd/random-engine

FROM alpine:3.20
RUN apk add --no-cache wget \
  && adduser -D -H -u 10001 engine
USER engine
WORKDIR /app
COPY --from=build /out/random-engine /app/random-engine
ENV RANDOM_ENGINE_ADDR=:8091
EXPOSE 8091
HEALTHCHECK --interval=10s --timeout=2s --start-period=3s --retries=5 \
  CMD wget -qO- http://127.0.0.1:8091/healthz >/dev/null || exit 1
ENTRYPOINT ["/app/random-engine"]
