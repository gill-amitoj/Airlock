"""
Unit tests for outbound URL validation.

resolve_hostname is patched throughout so these tests never touch the network
and their results do not depend on live DNS.
"""

import pytest
from unittest.mock import patch

from src.services.url_guard import (
    BlockedUrlError,
    validate_url,
    _is_public_address,
)


ALLOWED = ["catfact.ninja", "jsonplaceholder.typicode.com"]

# Any routable address; the allowlist is what is under test in those cases.
PUBLIC_IP = "93.184.216.34"


class TestAllowlist:
    """Hostname allowlist enforcement."""

    @patch("src.services.url_guard.resolve_hostname", return_value=[PUBLIC_IP])
    def test_allowed_host_passes(self, mock_resolve):
        """An allowlisted host resolving publicly is permitted."""
        validate_url("https://catfact.ninja/fact", ALLOWED)

        mock_resolve.assert_called_once_with("catfact.ninja")

    @patch("src.services.url_guard.resolve_hostname", return_value=[PUBLIC_IP])
    def test_allowed_host_is_case_insensitive(self, mock_resolve):
        """Host matching ignores case on both sides."""
        validate_url("https://CatFact.Ninja/fact", ["CATFACT.NINJA"])

    @patch("src.services.url_guard.resolve_hostname", return_value=[PUBLIC_IP])
    def test_blocked_host_raises(self, mock_resolve):
        """A host outside the allowlist is rejected."""
        with pytest.raises(BlockedUrlError, match="not in the allowlist"):
            validate_url("https://evil.example.com/steal", ALLOWED)

    @patch("src.services.url_guard.resolve_hostname", return_value=[PUBLIC_IP])
    def test_blocked_host_is_not_resolved(self, mock_resolve):
        """A non-allowlisted host is rejected before any DNS lookup happens."""
        with pytest.raises(BlockedUrlError):
            validate_url("https://evil.example.com/steal", ALLOWED)

        mock_resolve.assert_not_called()

    @patch("src.services.url_guard.resolve_hostname", return_value=[PUBLIC_IP])
    def test_subdomain_of_allowed_host_is_blocked(self, mock_resolve):
        """Matching is exact - a subdomain does not inherit the parent's entry."""
        with pytest.raises(BlockedUrlError, match="not in the allowlist"):
            validate_url("https://evil.catfact.ninja/fact", ALLOWED)

    @patch("src.services.url_guard.resolve_hostname", return_value=[PUBLIC_IP])
    def test_empty_allowlist_blocks_everything(self, mock_resolve):
        """An empty allowlist permits nothing."""
        with pytest.raises(BlockedUrlError, match="not in the allowlist"):
            validate_url("https://catfact.ninja/fact", [])


class TestPrivateAddressBlocking:
    """Allowlisted hosts that resolve somewhere internal must still be blocked."""

    @pytest.mark.parametrize(
        "private_ip",
        [
            "127.0.0.1",        # loopback
            "127.1.2.3",        # rest of 127/8
            "10.0.0.1",         # private class A
            "192.168.1.1",      # private class C
            "172.16.0.1",       # private class B, low end
            "172.31.255.254",   # private class B, high end
            "169.254.169.254",  # cloud metadata endpoint
            "::1",              # IPv6 loopback
            "fd00::1",          # IPv6 unique local
        ],
    )
    def test_private_address_raises(self, private_ip):
        """An allowlisted host pointing at internal space is rejected."""
        with patch("src.services.url_guard.resolve_hostname", return_value=[private_ip]):
            with pytest.raises(BlockedUrlError, match="non-public address"):
                validate_url("https://catfact.ninja/fact", ALLOWED)

    def test_any_private_address_in_set_raises(self):
        """If a host resolves to several addresses, one bad one is enough."""
        with patch(
            "src.services.url_guard.resolve_hostname",
            return_value=[PUBLIC_IP, "169.254.169.254"],
        ):
            with pytest.raises(BlockedUrlError, match="non-public address"):
                validate_url("https://catfact.ninja/fact", ALLOWED)

    @pytest.mark.parametrize(
        "public_ip",
        ["93.184.216.34", "8.8.8.8", "172.32.0.1", "2606:2800:220:1::1"],
    )
    def test_public_addresses_pass(self, public_ip):
        """Routable addresses are allowed, including 172.32 just past the private block."""
        with patch("src.services.url_guard.resolve_hostname", return_value=[public_ip]):
            validate_url("https://catfact.ninja/fact", ALLOWED)


class TestFailClosed:
    """Anything that cannot be checked is refused rather than allowed through."""

    def test_resolution_failure_blocks(self):
        """A hostname that will not resolve is blocked, not permitted."""
        with patch(
            "src.services.url_guard.resolve_hostname",
            side_effect=OSError("name resolution failed"),
        ):
            with pytest.raises(BlockedUrlError, match="could not be resolved"):
                validate_url("https://catfact.ninja/fact", ALLOWED)

    def test_no_addresses_blocks(self):
        """A host that resolves to nothing is blocked."""
        with patch("src.services.url_guard.resolve_hostname", return_value=[]):
            with pytest.raises(BlockedUrlError, match="no addresses"):
                validate_url("https://catfact.ninja/fact", ALLOWED)

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://catfact.ninja/fact",
            "gopher://catfact.ninja/",
        ],
    )
    def test_non_http_scheme_raises(self, url):
        """Only http and https are permitted."""
        with pytest.raises(BlockedUrlError, match="scheme"):
            validate_url(url, ALLOWED)

    def test_missing_hostname_raises(self):
        """A URL with no host is rejected."""
        with pytest.raises(BlockedUrlError, match="no hostname"):
            validate_url("https:///just/a/path", ALLOWED)

    @pytest.mark.parametrize("url", ["", None])
    def test_empty_url_raises(self, url):
        """Empty or non-string URLs are rejected."""
        with pytest.raises(BlockedUrlError, match="empty or not a string"):
            validate_url(url, ALLOWED)


class TestIsPublicAddress:
    """Direct tests for the address classifier."""

    def test_unparseable_address_is_not_public(self):
        """A value that is not an IP is treated as unsafe."""
        assert _is_public_address("not-an-ip") is False

    def test_public_address(self):
        assert _is_public_address("93.184.216.34") is True

    def test_unspecified_address_is_not_public(self):
        assert _is_public_address("0.0.0.0") is False


class TestBlockedUrlError:
    """The exception should be legible in execution logs."""

    def test_message_names_url_and_reason(self):
        error = BlockedUrlError("https://evil.example.com", "host is not in the allowlist")

        assert "https://evil.example.com" in str(error)
        assert "not in the allowlist" in str(error)
        assert error.url == "https://evil.example.com"
