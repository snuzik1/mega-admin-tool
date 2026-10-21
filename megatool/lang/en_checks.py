"""Availability checks strings (English)."""

DATA = {
    "checks": {
        "badList": "The checks list must be an array",
        "tooMany": "Too many checks (limit {limit})",
        "badType": "Unknown check type",
        "badUrl": "An HTTP check needs a valid http(s):// URL without credentials",
        "badHost": "Enter a valid host name or IP address",
        "notFound": "Check not found",
        "source": "Availability checks",
        "detail": {
            "redirects": "Too many redirects (limit 3)",
            "redirectScheme": "Redirect to an unsupported scheme (only HTTP and HTTPS are allowed)",
            "error": "Error: {error}",
            "httpStatus": "Response code {status}, expected {expected}",
            "keyword": "The response does not contain “{keyword}”",
            "httpOk": "HTTP {status} in {ms} ms",
            "tcpOk": "Port open in {ms} ms",
            "tlsVerify": "Certificate verification failed: {error}",
            "tlsOk": "Certificate valid for {days} days (until {expires})",
            "tlsWarn": "Certificate expires in {days} days (until {expires})",
            "tlsExpired": "Certificate expired {days} days ago ({expires})",
            "tlsOkNoExpiry": "TLS connection established in {ms} ms, certificate expiry unknown",
        },
        "alert": {
            "down": "Check is down: {name}",
            "expiring": "Certificate is expiring: {name}",
        },
    },
}
