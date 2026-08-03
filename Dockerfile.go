# Copyright 2026 Mael Klingler
# Licensed under the Apache License, Version 2.0

# Build stage
FROM golang:1.23-alpine AS builder

RUN apk add --no-cache git

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /orchestrator ./cmd/orchestrator

# Runtime stage
FROM alpine:3.20

RUN apk add --no-cache ca-certificates git && \
    addgroup -g 1000 hivemind && \
    adduser -u 1000 -G hivemind -s /bin/sh -D hivemind

WORKDIR /app

COPY --from=builder /orchestrator /app/orchestrator

RUN mkdir -p /app/data && chown -R hivemind:hivemind /app

USER hivemind

EXPOSE 8080

ENTRYPOINT ["/app/orchestrator"]
CMD ["-serve"]