"""Config bundle messages (English)."""

DATA = {
    "bundles": {
        "needName": "Enter a bundle name",
        "badId": "Invalid bundle id",
        "notFound": "Bundle not found: {id}",
        "badPath": (
            "Invalid file path: '..', absolute paths, '~', ':' and "
            "control characters are not allowed"
        ),
        "presetNotFound": "Preset not found: {preset}",
        "fileExists": "File already exists: {path}",
        "fileNotFound": "File not found: {path}",
        "fileTooBig": "File is larger than {mb} MB — reduce its size",
        "tooManyFiles": "A bundle cannot contain more than {limit} files",
        "readFailed": "Failed to read the file: {error}",
        "writeFailed": "Failed to write the file: {error}",
        "deleteFailed": "Failed to delete: {error}",
        "exportFailed": "Failed to build the ZIP archive: {error}",
    },
}
