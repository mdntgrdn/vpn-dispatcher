# vpn-dispatcher

Personal VPN **dispatcher** for a single VPS: phones and laptops connect once
over AmneziaWG, and the server decides **where each flow exits** — corporate
VPN, another WireGuard peer, an obfuscated AWG uplink, or the host WAN.

### Why it exists

Corporate access usually means a separate Forti/Cisco client on every device.
Censorship-resistant tunnels want AmneziaWG obfuscation. Russian sites are often
faster (and safer for banking/gov) when they go out on the VPS public IP, not
through a foreign tunnel.

This project puts that split on the **server**: one inbound tunnel for users,
then policy-based egress. Clients and deploys are managed from the controller;
the VPS only runs the container.

### What it does

```
phone / laptop
    │  AmneziaWG (awg-in)
    ▼
┌─────────────────────────────────────────┐
│  Docker gateway on the VPS              │
│                                         │
│  Xray classifies traffic                │
│    → Forti / Cisco / WG  (corp policies)│
│    → direct WAN          (geo policies) │
│    → AWG fallback        (everything else)│
└─────────────────────────────────────────┘
```

Deployed as one container that runs:

| Component | Role |
|--|--|
| **awg-in** | Inbound AmneziaWG server; users and their `.conf` files are managed from the controller |
| **Xray** | Classifies flows by domain, CIDR, GeoIP, and GeoSite; stamps an fwmark |
| **forti0** | Optional FortiGate SSL VPN (`openfortivpn`, browser SAML) |
| **cisco0** | Optional Cisco AnyConnect (`openconnect` + password/TOTP) |
| **wg-out** | Optional WireGuard client (one peer) |
| **AWG fallback** | Optional default exit for unmatched traffic |

Routing order when exits are enabled: **user policies** (Forti / Cisco / WG) →
**configured GeoIP/GeoSite → direct WAN** → **AWG fallback** (or WAN if AWG out
is off). Toggle with `XRAY_DIRECT_GEO` (default `true`); categories are
`XRAY_DIRECT_GEOSITE` / `XRAY_DIRECT_GEOIP` (`category-ru` / `ru`). Each exit is
independent and off by default.

`HOST_NETWORK` in `.env` chooses how the container is networked and how AWG
fallback is wired:

- `true` — host network; mark traffic onto a host uplink iface (`AWG_OUT_INTERFACE`);
- `false` — Docker bridge; local `awg-out` client to a peer (often
  `host.docker.internal`).

Deploy renders `docker-compose.yml` from a template so that mode is baked in.

## Requirements

On the controller:

- Python 3.12+;
- Poetry (dependencies only; the project is not installed as a package);
- SSH access to a Debian/Ubuntu host.

Docker is installed by the playbook on the server. The kernel must support TUN,
PPP, and WireGuard. AWG runs via userspace `amneziawg-go`.

## Setup

```bash
poetry install
cp .env.example .env   # required; scripts fail if .env is missing
```

Fill in `.env`. The full contract and examples are in `.env.example`.

Main variable groups:

- `DEPLOY_*` — SSH host, user, port, key, and remote directory;
- `HOST_NETWORK` — `true` = host netns + uplink iface; `false` = bridge +
  `awg-out` client to `host.docker.internal`;
- `PUBLIC_HOST`, `AWG_LISTEN_PORT`, `AWG_SUBNET` — inbound AWG
  (`Jc/Jmin/Jmax/S1–S4/H1–H4`, optional `I1–I5`);
- `AWG_OUT_*` — fallback (host iface/`SNAT` or peer client — see `.env.example`);
- `WG_OUT_*` — single WireGuard peer;
- `FORTI_*` — FortiGate SAML endpoint (`FORTI_HOST`, realm, trusted-cert);
- `CISCO_*` — endpoint, user, password, and TOTP (URI or raw Base32 secret).

Each optional exit is toggled with its own flag:

```dotenv
FORTI_ENABLED=false
CISCO_ENABLED=false
WG_OUT_ENABLED=false
```

A disabled client is not started, gets no routing table / Xray outbound, and is
skipped by the healthcheck.

`AWG_OUT_ENABLED` is optional. With `HOST_NETWORK=true`, unmatched traffic is
marked `MARK_AWG` and sent to `AWG_OUT_INTERFACE`: `awg-in.conf` (including
`PostUp`/`PostDown` for mark/SNAT) is **built on the controller** by
`scripts.lib.render_awg_in` and copied by Ansible. The container only runs
`awg-quick up`. It does not rewrite host table `210`.

With `HOST_NETWORK=false`, a local `awg-out` client is brought up with one peer
(`AWG_OUT_ENDPOINT`, usually `host.docker.internal:…`); policy routing and
masquerade stay inside the container. Host-uplink PostUp lines are not added to
`awg-in`.

If `AWG_OUT_ENABLED=false`, unmatched traffic goes direct via WAN.

With `HOST_NETWORK=true`, set on the host: `net.ipv4.ip_forward=1` and
`net.ipv4.conf.all.src_valid_mark=1` (Docker does not set them).

`DEPLOY_HOST` is the SSH address; `PUBLIC_HOST` is the IP/domain written into
inbound AWG client configs. `WAN_IFACE` is the public NIC (host network); empty
means the lowest-metric IPv4 default route.

### Secrets

Passwords, private keys, and TOTP (`otpauth://totp/...` or raw Base32) are plain
variables in `.env` (e.g. `CISCO_PASSWORD`, `CISCO_TOTP_URI`,
`WG_OUT_PRIVATE_KEY`). Ansible writes them under `secrets/` with mode `0600` and
`no_log`; they do not go into inventory or Compose `.env`.

If `WG_OUT_PRIVATE_KEY` is empty, the `wg-out` key is generated on first start
and persisted; the public key is printed to the log.

## Console scripts

All commands run from the project root:

```bash
poetry run python scripts/<name>.py
```

Helpers live under `scripts/lib/` and are not meant to be invoked directly.

### Lifecycle

| Script | Purpose |
|--|--|
| `deploy.py` | Renders `awg-in.conf` on the controller, copies artifacts, builds the image, starts the container (always `--force-recreate`). With `FORTI_ENABLED=true`, then runs Forti SAML on `127.0.0.1:FORTI_SAML_PORT` |
| `restart.py` | `docker compose restart` on the server |
| `status.py` | Healthcheck inside the container (xray / awg-in / tunnels) |
| `logs.py` | Container logs |
| `destroy.py` | `awg-quick down` (runs PostDown: mark/SNAT/FORWARD cleanup), then `compose down`. Does not delete `$DEPLOY_PATH` or `data/` |

Useful `deploy` flags:

- `--check` — Ansible check mode (no changes on the server).
- `--skip-forti` — skip post-deploy Forti SAML login.
- `--no-browser` / `--timeout N` — same meaning as `forti_reconnect.py` (deploy default timeout 120s).

### Forti / Cisco

| Script | Purpose |
|--|--|
| `forti_reconnect.py` | Kills `openfortivpn` in the container (restart loop brings it back), then browser SAML on `127.0.0.1:FORTI_SAML_PORT`. Use whenever the Forti session dies after deploy. Other tunnels untouched |
| `cisco_reconnect.py` | Kills `openconnect`; restart loop reconnects with password+TOTP. Waits for `cisco0` |

Flags: `--no-browser` (Forti), `--timeout N` (default 60s for Forti).

Forti requires browser SSO — password+OTP via `/remote/logincheck` is not
supported here. `deploy.py` runs SAML automatically when `FORTI_ENABLED=true`
(unless `--skip-forti` / `--check`).

### Inbound AWG users

These commands run **locally** and only use Ansible for `docker exec` / copy on
the server. Client-manager code is not baked into the image.

| Script | Purpose |
|--|--|
| `add_clients.py` | Creates peers, writes `.conf` files, updates `awg-in` via `syncconf` (`-u "Alice,Bob"`, `-o ./clients`) |
| `list_clients.py` | Lists clients (`--format table\|json\|yaml`) |
| `remove_clients.py` | Removes clients by name (`-u "Alice"`) |

```bash
poetry run python scripts/add_clients.py -u "Alice,Bob"
poetry run python scripts/add_clients.py -u "Alice" -o ./clients
poetry run python scripts/list_clients.py
poetry run python scripts/list_clients.py --format json
poetry run python scripts/remove_clients.py -u "Alice"
```

Peers are updated by re-rendering `awg-in.conf` locally and running
`awg syncconf` without restarting the interface. Names are checked for
case-insensitive duplicates and slug collisions. Local `.conf` files go under
`clients/` (gitignored); server copies live in `$DEPLOY_PATH/data/clients/`.

## Policies

Domains and networks are comma-separated lists in `.env`:

```dotenv
POLICY_FORTI_DOMAINS=domain:corp.example.com,full:portal.example.com
POLICY_FORTI_CIDRS=10.20.0.0/16,10.21.30.5/32
POLICY_CISCO_DOMAINS=domain:partner.example.org
POLICY_CISCO_CIDRS=10.30.0.0/16
POLICY_WG_DOMAINS=domain:private.example.net
POLICY_WG_CIDRS=10.40.0.0/16
```

A bare domain becomes `domain:`. Xray expressions `domain:`, `full:`,
`regexp:`, and `keyword:` are allowed. User policies (Forti/Cisco/WG) run before
the direct GeoIP/GeoSite rules, otherwise corporate destinations that also match
geo would go `direct`. Unmatched traffic uses the AWG fallback (mark → host
uplink) or WAN.

Geo → direct is set in `.env`:

```dotenv
XRAY_DIRECT_GEO=true
XRAY_DIRECT_GEOSITE=category-ru
XRAY_DIRECT_GEOIP=ru
# Temporary test: XRAY_DIRECT_GEO=false — no RU split, all unmatched via AWG.
```

Xray learns the domain from visible DNS/HTTP/TLS/QUIC. ECH and non-standard
encrypted DNS may hide the name; CIDR and GeoIP still work.

## Adding other VPN clients

Exits are independent plugins under `container/egress/`. The registry wires each
module into Xray, policy routing, kill-switch, healthcheck, restart loop, and
secret delivery. Adding OpenVPN does not require changing Forti/Cisco/WG/AWG
code. Scaffold and notes: `container/egress/README.md`.

## How deploy works

`deploy.py` runs Ansible **from the controller**: the server builds the image
and starts the container. Only `container/` is copied for the build, then
removed. Left on disk: `docker-compose.yml`, `Dockerfile`, `.env*`, `secrets/`,
and `data/`. CLI and Ansible playbooks stay on the controller.

After changing `.env`, credentials, TOTP, or container code, run `deploy.py`
again.

## Diagnostics

On the server:

```bash
cd /opt/vpn-dispatcher
docker compose ps
docker compose logs --tail=200
docker exec vpn-dispatcher awg show
docker exec vpn-dispatcher wg show
docker exec vpn-dispatcher ip rule
docker exec vpn-dispatcher nft list table inet vpn_dispatcher
```

Forti, Cisco, and WG use separate routing tables. If a tunnel is down, that
table gets `unreachable default`. AWG fallback lives in controller-rendered
`awg-in` PostUp and does not rewrite the host uplink table. When AWG out is
disabled, unmatched traffic goes straight to the internet.
