"""Built-in TLS messages (English)."""

DATA = {
    "tls": {
        "opensslMissing": (
            "Creating a self-signed certificate requires openssl. Install it "
            "(for example: sudo apt install openssl) or provide an existing "
            "--tls-cert/--tls-key pair."
        ),
        "generateFailed": "Could not generate the certificate: {error}",
        "partial": (
            "Only one TLS file was found: certificate {cert}, key {key}. "
            "Remove the incomplete pair or generate a new certificate."
        ),
        "certMissing": "Certificate file not found: {path}",
        "certSymlink": "Certificate must not be a symbolic link: {path}",
        "certRead": "Could not read the certificate: {error}",
        "certInvalid": "Certificate and key do not match: {error}",
        "keyMissing": "Key file not found: {path}",
        "keySymlink": "Key must not be a symbolic link: {path}",
        "keyOwner": "Key must be owned by the current user: {path}",
        "keyPermissions": "Key must have mode 0600, currently {mode}: {path}",
        "keyDir": "Invalid key directory: {path}",
        "keyDirPermissions": "Key directory must be private (mode 0700): {path}",
        "dirSymlink": "TLS directory must not be a symbolic link: {path}",
        "dirMissing": "TLS directory not found: {path}",
        "dirOwner": "TLS directory must be owned by the current user: {path}",
        "dirPermissions": "Could not fix TLS directory permissions {path}: {error}",
        "dirCreate": "Could not create TLS directory {path}: {error}",
        "externalRequired": (
            "An external bind requires HTTPS. Enable TLS and create a certificate "
            "(python -m megatool --setup) or explicitly allow insecure HTTP."
        ),
    },
}
