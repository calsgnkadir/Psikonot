"""
backend/middleware/ip_allowlist.py — Network Level Isolation Middleware
========================================================================
Restricts incoming API access strictly to authorized internal CIDR subnets,
private VPNs, and loopback addresses. Blocks public internet IP ranges.
Hardened against X-Forwarded-For header spoofing attacks.
"""

import logging
import os
import ipaddress
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("vhv.ipallowlist")


DEFAULT_ALLOWED_SUBNETS = [
    "127.0.0.1/32",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
]


# Names the test client and some servers give the peer instead of an address.
_LOOPBACK_NAMES = {"testclient", "localhost"}


def _trusted_proxy_networks() -> list:
    """Loopback plus every TRUSTED_PROXIES entry (an address or a CIDR range)."""
    networks = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]
    for entry in os.getenv("TRUSTED_PROXIES", "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning(f"[IPAllowlist Warning] Invalid TRUSTED_PROXIES entry ignored: {entry}")
    return networks


def _is_trusted_proxy(host: str, networks: list) -> bool:
    if host in _LOOPBACK_NAMES:
        return True
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip_obj in net for net in networks)


def resolve_secure_client_ip(request: Request) -> str:
    """
    The address of the client, as seen by the first proxy we do not run.

    X-Forwarded-For is read only when TRUST_PROXIES=true and the socket peer is
    loopback or a TRUSTED_PROXIES entry. Each proxy APPENDS the address it saw,
    so only the right end of the list is written by our own proxies; the left end
    is whatever the client sent. The list is walked from the right, skipping our
    own proxies, and the first other address is the client. A value that is not
    an address stops the walk and the peer is used: nothing to the left of a
    broken hop can be trusted. X-Real-IP is not read, since a proxy that does not
    set it passes on whatever the client sent.
    """
    peer_ip = request.client.host if request.client else "127.0.0.1"

    if os.getenv("TRUST_PROXIES", "false").lower() not in ("true", "1", "yes"):
        return peer_ip

    networks = _trusted_proxy_networks()
    if not _is_trusted_proxy(peer_ip, networks):
        return peer_ip

    # Several X-Forwarded-For headers are one list, in order.
    hops = [hop.strip() for value in request.headers.getlist("X-Forwarded-For") for hop in value.split(",")]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer_ip
        if not _is_trusted_proxy(hop, networks):
            return hop

    return peer_ip


def ip_allowlist_enabled() -> bool:
    """VHV_IP_ALLOWLIST_ENABLED, like the other VHV_ settings. The old name,
    VIP_IP_ALLOWLIST_ENABLED, is still read, so a deployment that set it keeps
    its setting. On unless explicitly turned off."""
    value = os.getenv("VHV_IP_ALLOWLIST_ENABLED", os.getenv("VIP_IP_ALLOWLIST_ENABLED", "true"))
    return value.lower() in ("true", "1", "yes")


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """
    Middleware enforcing IP allowlisting for network-level isolation.
    """

    def __init__(self, app):
        super().__init__(app)
        self.enabled = ip_allowlist_enabled()

        custom_networks = os.getenv("ALLOWLISTED_NETWORKS", "").strip()
        subnet_strings = [s.strip() for s in custom_networks.split(",") if s.strip()] if custom_networks else DEFAULT_ALLOWED_SUBNETS

        self.allowed_networks = []
        for s in subnet_strings:
            try:
                self.allowed_networks.append(ipaddress.ip_network(s, strict=False))
            except ValueError:
                logger.warning(f"[IPAllowlist Warning] Invalid CIDR subnet format ignored: {s}")

    def _get_client_ip(self, request: Request) -> str:
        return resolve_secure_client_ip(request)

    def is_ip_allowed(self, ip_str: str) -> bool:
        if not self.enabled:
            return True

        if ip_str in ("testclient", "localhost", "127.0.0.1", "::1"):
            return True

        try:
            ip_obj = ipaddress.ip_address(ip_str)
            return any(ip_obj in net for net in self.allowed_networks)
        except ValueError:
            return False

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        # Allow container health checks without network restriction
        if request.url.path == "/api/v1/health":
            return await call_next(request)

        client_ip = self._get_client_ip(request)

        if not self.is_ip_allowed(client_ip):
            from core.services.alert_service import alert_service
            alert_service.raise_alert(
                alert_type="UNAUTHORIZED_IP_ACCESS",
                severity="HIGH",
                title="Blocked Unauthorized IP Connection",
                description=f"Connection attempt from non-allowlisted IP: {client_ip} to {request.url.path}",
                client_ip=client_ip
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "Network Security Violation: IP address not authorized for PsikoNot access.",
                    "client_ip": client_ip
                }
            )

        return await call_next(request)
