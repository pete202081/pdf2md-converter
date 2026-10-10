# Official MinIO community source, pinned to the security release commit.
# Public minio/minio and minio/mc registry images are no longer reliably pullable.
FROM golang:1.24.8-alpine AS builder
RUN apk add --no-cache git ca-certificates
ARG MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
WORKDIR /src
RUN git init . && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth 1 origin tag RELEASE.2025-10-15T17-29-55Z \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$MINIO_COMMIT"
RUN CGO_ENABLED=0 go build -trimpath -o /minio .

FROM alpine:3.21
RUN apk add --no-cache ca-certificates curl \
    && addgroup -g 1000 minio && adduser -D -u 1000 -G minio minio \
    && mkdir /data && chown minio:minio /data
COPY --from=builder /minio /usr/local/bin/minio
USER minio
EXPOSE 9000 9001
HEALTHCHECK --interval=10s --timeout=5s --retries=12 \
    CMD curl -fsS http://localhost:9000/minio/health/ready >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data", "--console-address", ":9001"]
