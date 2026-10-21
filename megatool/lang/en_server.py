"""Server-side security messages (loopback/Host/Origin)."""

DATA = {
    "server": {
        "tokenRequired": "MegaTool access token required",
        "tooManyAttempts": "Too many failed token attempts. Try again in a minute.",
        "jobNotFound": "job not found",
        "noWebDir": "interface directory not found: {path}",
        "tooLarge": "request body is too large (limit {mb} MB)",
        "badHost": (
            "Request rejected: Host «{host}» is not allowed. The app listens on "
            "localhost only; add the name to server.allowed_hosts to allow it."
        ),
        "badOrigin": "Request rejected: origin {origin} does not match the application address.",
    },
}
