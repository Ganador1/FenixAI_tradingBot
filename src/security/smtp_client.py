"""SSRF-resistant SMTP connectivity checks with verified TLS."""

from __future__ import annotations

import ipaddress
import os
import smtplib
import socket
import ssl
from dataclasses import dataclass


class SMTPDestinationError(ValueError):
    """Raised when an SMTP destination violates the outbound network policy."""


@dataclass(frozen=True)
class ResolvedSMTPDestination:
    hostname: str
    address: str
    port: int


def _allowed_hosts() -> set[str]:
    return {
        value.strip().lower().rstrip(".")
        for value in os.getenv("FENIX_SMTP_ALLOWED_HOSTS", "").split(",")
        if value.strip()
    }


def resolve_smtp_destination(host: str, port: int) -> ResolvedSMTPDestination:
    """Resolve once and pin a policy-approved SMTP destination."""
    normalized = host.strip().lower().rstrip(".")
    if not normalized or len(normalized) > 253:
        raise SMTPDestinationError("SMTP host is invalid")
    if port not in {465, 587}:
        raise SMTPDestinationError("SMTP port must be 465 or 587")

    allowlist = _allowed_hosts()
    explicitly_allowed = normalized in allowlist
    if allowlist and not explicitly_allowed:
        raise SMTPDestinationError("SMTP host is not in FENIX_SMTP_ALLOWED_HOSTS")

    try:
        resolved = socket.getaddrinfo(
            normalized,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except socket.gaierror as exc:
        raise SMTPDestinationError("SMTP host could not be resolved") from exc

    addresses: list[str] = []
    for _family, _socktype, _proto, _canonname, sockaddr in resolved:
        address = str(sockaddr[0]).split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise SMTPDestinationError("SMTP resolution returned an invalid IP address") from exc
        if not explicitly_allowed and not parsed.is_global:
            raise SMTPDestinationError(
                "SMTP host resolves to a non-public address; explicitly allowlist "
                "trusted internal SMTP hosts"
            )
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise SMTPDestinationError("SMTP host did not resolve to a usable address")
    # Pin one already-validated address so a second DNS lookup cannot rebind
    # the connection to loopback, link-local, or an internal service.
    return ResolvedSMTPDestination(normalized, addresses[0], port)


class _PinnedSMTP(smtplib.SMTP):
    def __init__(self, destination: ResolvedSMTPDestination, timeout: float):
        self._pinned_address = destination.address
        super().__init__(host=destination.hostname, port=destination.port, timeout=timeout)

    def _get_socket(self, host: str, port: int, timeout: float):
        return socket.create_connection(
            (self._pinned_address, port),
            timeout,
            self.source_address,
        )


class _PinnedSMTPSSL(smtplib.SMTP_SSL):
    def __init__(
        self,
        destination: ResolvedSMTPDestination,
        timeout: float,
        context: ssl.SSLContext,
    ):
        self._pinned_address = destination.address
        self._tls_hostname = destination.hostname
        super().__init__(
            host=destination.hostname,
            port=destination.port,
            timeout=timeout,
            context=context,
        )

    def _get_socket(self, host: str, port: int, timeout: float):
        raw_socket = socket.create_connection(
            (self._pinned_address, port),
            timeout,
            self.source_address,
        )
        return self.context.wrap_socket(raw_socket, server_hostname=self._tls_hostname)


def test_smtp_connection(
    *,
    host: str,
    port: int,
    username: str = "",
    password: str = "",
    timeout: float = 5.0,
) -> None:
    """Connect, establish verified TLS, and optionally authenticate."""
    destination = resolve_smtp_destination(host, port)
    context = ssl.create_default_context()

    if port == 465:
        client: smtplib.SMTP = _PinnedSMTPSSL(destination, timeout, context)
    else:
        client = _PinnedSMTP(destination, timeout)

    with client:
        client.ehlo()
        if port == 587:
            client.starttls(context=context)
            client.ehlo()
        if username:
            if not password:
                raise ValueError("SMTP password is not configured")
            client.login(username, password)
