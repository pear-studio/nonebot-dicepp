"""Network-related shared utilities."""


def format_url_host(host: str) -> str:
    """Format a host for use in a URL, wrapping IPv6 addresses in brackets.

    ``127.0.0.1`` and other IPv4 hosts are returned unchanged.
    IPv6 hosts (detected by the presence of ``:``) are wrapped in ``[...]``
    unless they are already bracketed.
    """
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host
