"""Server monitoring strings (English)."""

DATA = {
    "hosts": {
        "badList": "The server list must be an array",
        "tooMany": "Too many servers (limit: {limit})",
        "badProbe": "Unknown server probe type",
        "badUrl": "An HTTP probe needs a valid http(s):// URL without credentials",
        "badHost": "Enter a valid hostname or IP address",
        "notFound": "Server not found",
        "source": "Server monitoring",
        "alert": {
            "down": "Server is down: {name}",
            "noData": "No successful checks yet",
        },
    },
}
