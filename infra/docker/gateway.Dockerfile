# syntax=docker/dockerfile:1.7
FROM rust:1.95-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml Cargo.lock* ./
COPY rust-toolchain.toml ./
COPY src/ src/
COPY tests/ tests/
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/app/target \
    cargo build --release && cp target/release/archiviste-gateway /usr/local/bin/

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/bin/archiviste-gateway /usr/local/bin/
EXPOSE 8080
CMD ["archiviste-gateway"]
