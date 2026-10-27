"""Server-side security messages (loopback/Host/Origin)."""

DATA = {
    "server": {
        "jobNotFound": "job not found",
        "noWebDir": "interface directory not found: {path}",
        "tooLarge": "request body is too large (limit {mb} MB)",
        "badHost": (
            "Request rejected: Host «{host}» is not allowed. Local names and IPs are "
            "allowed automatically; add any other name to server.allowed_hosts."
        ),
        "badOrigin": "Request rejected: origin {origin} does not match the application address.",
    },
}
