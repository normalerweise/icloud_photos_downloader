FROM alpine:3.18 AS runtime_amd64_none
ENV MUSL_LOCPATH="/usr/share/i18n/locales/musl"
RUN apk update && apk add --no-cache tzdata musl-locales musl-locales-lang
WORKDIR /app
COPY dist/icloud-*.*.*-linux-musl-amd64 icloud
COPY dist/icloudpd-*.*.*-linux-musl-amd64 icloudpd
COPY dist/icloudpd_sync-*.*.*-linux-musl-amd64 icloudpd-sync

FROM alpine:3.18 AS runtime_arm64_none
ENV MUSL_LOCPATH="/usr/share/i18n/locales/musl"
RUN apk update && apk add --no-cache tzdata musl-locales musl-locales-lang
WORKDIR /app
COPY dist/icloud-*.*.*-linux-musl-arm64 icloud
COPY dist/icloudpd-*.*.*-linux-musl-arm64 icloudpd
COPY dist/icloudpd_sync-*.*.*-linux-musl-arm64 icloudpd-sync

FROM alpine:3.18 AS runtime_arm_v7
ENV MUSL_LOCPATH="/usr/share/i18n/locales/musl"
RUN apk update && apk add --no-cache tzdata musl-locales musl-locales-lang
WORKDIR /app
COPY dist/icloud-*.*.*-linux-musl-arm32v7 icloud
COPY dist/icloudpd-*.*.*-linux-musl-arm32v7 icloudpd
COPY dist/icloudpd_sync-*.*.*-linux-musl-arm32v7 icloudpd-sync

FROM runtime_${TARGETARCH}_${TARGETVARIANT:-none} AS runtime
ENV TZ=UTC
EXPOSE 8080
WORKDIR /app
RUN chmod +x /app/icloud /app/icloudpd /app/icloudpd-sync

# Use a shell script to allow command selection
COPY <<EOF /app/entrypoint.sh
#!/bin/sh
# If first argument is 'icloud' or 'icloudpd', run the corresponding binary
case "\$1" in
    icloud)
        shift
        exec /app/icloud "\$@"
        ;;
    icloudpd)
        shift
        exec /app/icloudpd "\$@"
        ;;
    icloudpd-sync)
        shift
        exec /app/icloudpd-sync "\$@"
        ;;
    *)
        echo "Error: You must specify 'icloud', 'icloudpd', or 'icloudpd-sync' as the first argument."
        echo "Usage: docker run <image> icloudpd [options]"
        echo "   or: docker run <image> icloudpd-sync [options]"
        echo "   or: docker run <image> icloud [options]"
        exit 1
        ;;
esac
EOF

RUN chmod +x /app/entrypoint.sh

# Default entrypoint allows command selection
ENTRYPOINT ["/app/entrypoint.sh"]
