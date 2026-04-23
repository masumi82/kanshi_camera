# =============================================================================
# Stage 1: Build uStreamer from source
# =============================================================================
FROM debian:bullseye-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    git \
    pkg-config \
    libevent-dev \
    libjpeg62-turbo-dev \
    libbsd-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --branch v6.16 --depth 1 https://github.com/pikvm/ustreamer.git /tmp/ustreamer \
    && cd /tmp/ustreamer \
    && make -j$(nproc) \
    && make install DESTDIR=/opt/ustreamer PREFIX=/usr/local

# =============================================================================
# Stage 2: Final runtime image
# =============================================================================
FROM debian:bullseye-slim

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    supervisor \
    libevent-2.1-7 \
    libevent-pthreads-2.1-7 \
    libjpeg62-turbo \
    libbsd0 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy uStreamer binary from builder stage
COPY --from=builder /opt/ustreamer/usr/local/bin/ustreamer /usr/local/bin/ustreamer

# Create non-root user with video group access
RUN groupadd -r kanshi \
    && useradd -r -g kanshi -G video -d /app -s /sbin/nologin kanshi

# Create application directories
RUN mkdir -p /app/src /app/src/static /var/lib/kanshi/retry /var/lib/kanshi/gallery /var/lib/kanshi/state /etc \
    && chown -R kanshi:kanshi /app /var/lib/kanshi

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

# Copy application files
COPY src/ /app/src/
COPY config/ /app/config/
COPY supervisord.conf /etc/supervisord.conf

# Ensure correct ownership
RUN chown -R kanshi:kanshi /app

EXPOSE 8080 8888

USER kanshi

CMD ["supervisord", "-c", "/etc/supervisord.conf"]
