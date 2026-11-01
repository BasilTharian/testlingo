"""Stops a public deployment from being used to reach private networks.

When Testlingo runs on a public host, anyone with access could ask it to open
http://localhost, 10.x.x.x or the cloud metadata address (169.254.169.254).
With BLOCK_PRIVATE_NETWORKS=1 those targets are refused.
"""
import ipaddress
import os
import socket
from urllib.parse import urlparse

ENABLED = os.environ.get("BLOCK_PRIVATE_NETWORKS", "0").lower() in ("1", "true", "yes")
# The app's own public address (e.g. https://testlingo.onrender.com) is always allowed,
# so the built-in demo shop and demo API keep working.
SELF_HOSTS = {h.strip().lower() for h in os.environ.get("PUBLIC_HOSTS", "").split(",") if h.strip()}
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    SELF_HOSTS.add(os.environ["RENDER_EXTERNAL_HOSTNAME"].lower())


class BlockedTarget(Exception):
    pass


def _private(ip):
    ip = ipaddress.ip_address(ip)
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def check_url(url):
    """Raise BlockedTarget if url points at a private / internal address."""
    if not ENABLED:
        return
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise BlockedTarget(f"Only http and https addresses can be tested here ({p.scheme}: is not allowed)")
    host = (p.hostname or "").lower()
    if not host:
        raise BlockedTarget("The address has no host name")
    if host in SELF_HOSTS:
        return
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror:
        raise BlockedTarget(f"Could not find the server {host} (DNS lookup failed)")
    for info in infos:
        if _private(info[4][0]):
            raise BlockedTarget(f"{host} is a private or internal address, which this online version does not test. "
                                "Run Testlingo on your own computer to test internal sites.")


def is_allowed(url):
    try:
        check_url(url)
        return True
    except BlockedTarget:
        return False
