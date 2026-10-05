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


def resolve_secure_client_ip(request: Request) -> str:
    """
    Extracts and validates client IP securely.
    X-Forwarded-For / X-Real-IP headers are ONLY trusted if:
    1. TRUST_PROXIES=true environment variable is explicitly enabled, AND
    2. The direct socket peer host is loopback, testclient, or a trusted proxy.
    Otherwise, returns direct request.client.host to prevent header spoofing bypasses.
    """
    peer_ip = request.client.host if request.client else "127.0.0.1"

    trust_proxies = os.getenv("TRUST_PROXIES", "false").lower() in ("true", "1", "yes")
    trusted_proxy_hosts = {"127.0.0.1", "::1", "testclient", "localhost"}

    extra_trusted = os.getenv("TRUSTED_PROXIES", "").strip()
    if extra_trusted:
        trusted_proxy_hosts.update([p.strip() for p in extra_trusted.split(",") if p.strip()])

    if trust_proxies and peer_ip in trusted_proxy_hosts:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            candidate = forwarded.split(",")[0].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            candidate = real_ip.strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass

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
