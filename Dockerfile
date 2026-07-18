FROM golang:1.25-bookworm AS awg-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth=1 https://github.com/amnezia-vpn/amneziawg-go.git /src/amneziawg-go \
    && make -C /src/amneziawg-go \
    && make -C /src/amneziawg-go install DESTDIR=/out
RUN git clone --depth=1 https://github.com/amnezia-vpn/amneziawg-tools.git /src/amneziawg-tools \
    && make -C /src/amneziawg-tools/src \
    && make -C /src/amneziawg-tools/src install DESTDIR=/out WITH_WGQUICK=yes

FROM debian:bookworm-slim AS openfortivpn-builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        autoconf automake build-essential ca-certificates git libssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth=1 --branch v1.24.1 \
        https://github.com/adrienverge/openfortivpn.git /src/openfortivpn \
    && cd /src/openfortivpn \
    && ./autogen.sh \
    && ./configure --prefix=/usr/local --sysconfdir=/etc \
    && make \
    && make install DESTDIR=/out

FROM ghcr.io/xtls/xray-core:latest AS xray

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONPATH=/app \
    WG_QUICK_USERSPACE_IMPLEMENTATION=amneziawg-go \
    XRAY_LOCATION_ASSET=/usr/local/share/xray

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates curl iproute2 iptables nftables openconnect \
            ppp procps python3 python3-yaml qrencode socat tini vpnc-scripts \
            wireguard-tools \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app /data /run/vpn-dispatcher /usr/local/share/xray

COPY --from=awg-builder /out/usr/bin/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=awg-builder /out/usr/bin/awg /usr/local/bin/awg
COPY --from=awg-builder /out/usr/bin/awg-quick /usr/local/bin/awg-quick
COPY --from=openfortivpn-builder /out/usr/local/bin/openfortivpn /usr/local/bin/openfortivpn
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY --from=xray /usr/local/share/xray/ /usr/local/share/xray/

COPY container/ /app/container/
RUN chmod 0755 /app/container/egress/_cisco_vpnc.py

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/app/container/entrypoint.py"]
