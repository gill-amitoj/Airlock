"""
Outbound URL validation for workflow steps.

Step URLs are untrusted input: they arrive from API callers and from the AI
workflow generator, which means a model can be talked into emitting any URL it
likes. The generator prompt lists a set of approved APIs, but a prompt is a
suggestion, not a boundary - this module is the boundary.

Two independent checks are applied:

1. The hostname must appear in an explicit allowlist (see Config.ALLOWED_HTTP_HOSTS).
2. Every address the hostname resolves to must be publicly routable, so an
   allowlisted name cannot be pointed at internal infrastructure - the cloud
   metadata endpoint at 169.254.169.254 being the classic target.

Both checks must pass. Validation fails closed: anything that cannot be parsed
or resolved is blocked.
"""

import ipaddress
import logging
import socket
from typing import Iterable, List
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = ("http", "https")


class BlockedUrlError(Exception):
    """
    Raised when a step URL fails validation.

    Carries the URL and the reason so the failure is legible in execution logs.
    Raised from inside a task handler, so the orchestrator's existing
    step-failure path reports it like any other step error.
    """

    def __init__(self, url: str, reason: str):
        self.url = url
        self.reason = reason
        super().__init__(f"Blocked URL '{url}': {reason}")


def resolve_hostname(hostname: str) -> List[str]:
    """
    Resolve a hostname to the list of IP addresses it maps to.

    Kept as a module-level function so tests can patch it without touching the
    network, and so a single hostname with several A/AAAA records is checked in
    full rather than on whichever address happens to be returned first.
    """
    infos = socket.getaddrinfo(hostname, None)
    # getaddrinfo returns (family, type, proto, canonname, sockaddr); the
    # address is always the first element of sockaddr for both IPv4 and IPv6.
    return list({info[4][0] for info in infos})


def _is_public_address(address: str) -> bool:
    """Check whether an IP address is publicly routable."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        # Unparseable address - treat as unsafe rather than guessing.
        return False

    # is_private covers 10/8, 172.16/12, 192.168/16 and the IPv6 equivalents.
    # The rest catch loopback (127/8, ::1), link-local (169.254/16, including
    # the 169.254.169.254 metadata endpoint), and reserved/unspecified space.
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str, allowed_hosts: Iterable[str]) -> None:
    """
    Validate an outbound step URL, raising BlockedUrlError if it is not permitted.

    Args:
        url: The fully-substituted URL the step intends to call.
        allowed_hosts: Permitted hostnames, matched exactly (case-insensitive).

    Raises:
        BlockedUrlError: If the scheme, hostname, or any resolved address is
            not permitted, or if the hostname cannot be resolved.
    """
    if not url or not isinstance(url, str):
        raise BlockedUrlError(str(url), "URL is empty or not a string")

    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise BlockedUrlError(url, f"URL could not be parsed: {e}") from e

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise BlockedUrlError(
            url,
            f"scheme '{parsed.scheme}' is not allowed "
            f"(permitted: {', '.join(ALLOWED_SCHEMES)})",
        )

    hostname = parsed.hostname
    if not hostname:
        raise BlockedUrlError(url, "URL has no hostname")

    hostname = hostname.lower()
    permitted = {h.strip().lower() for h in allowed_hosts if h and h.strip()}

    if hostname not in permitted:
        raise BlockedUrlError(
            url,
            f"host '{hostname}' is not in the allowlist",
        )

    # An allowlisted name can still point somewhere internal, so check where it
    # actually resolves to. Fail closed if resolution does not succeed.
    try:
        addresses = resolve_hostname(hostname)
    except Exception as e:
        raise BlockedUrlError(url, f"host '{hostname}' could not be resolved: {e}") from e

    if not addresses:
        raise BlockedUrlError(url, f"host '{hostname}' resolved to no addresses")

    for address in addresses:
        if not _is_public_address(address):
            raise BlockedUrlError(
                url,
                f"host '{hostname}' resolves to non-public address {address}",
            )
