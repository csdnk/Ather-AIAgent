FROM rust:1.85-bookworm AS build

WORKDIR /app
COPY . .
# Remote build hosts can have slow access to crates.io.  Keep Cargo's default
# 30-second transfer timeout from turning a transient network stall into a
# failed image build.
RUN CARGO_HTTP_TIMEOUT=600 CARGO_NET_RETRY=10 CARGO_HTTP_MULTIPLEXING=false \
    cargo build --release -p ae-server --bin ae-grpc

FROM debian:bookworm-slim

COPY --from=build /app/target/release/ae-grpc /usr/local/bin/ae-grpc

ENTRYPOINT ["ae-grpc"]
