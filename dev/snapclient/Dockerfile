FROM debian:bookworm-slim
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        snapclient \
    && rm -rf /var/lib/apt/lists/*
# Default: write decoded PCM to a file (no speaker). Override host via SNAPHOST.
ENV SNAPHOST=music-assistant
CMD ["bash", "-lc", "mkdir -p /audio && snapclient --host \"$SNAPHOST\" --player file --file /audio/out.pcm"]
