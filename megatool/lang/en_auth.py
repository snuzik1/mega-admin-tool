"""English authentication messages (CLI and API)."""

DATA = {
    "auth": {
        "required": "MegaTool sign-in required.",
        "badCredentials": "Invalid username or password.",
        "notConfigured": (
            "Authentication is not configured: run MegaTool in a terminal and set "
            "the administrator password. File: {path}"
        ),
        "tooMany": "Too many failed sign-in attempts. Try again later.",
        "payloadTooLarge": "The sign-in request body is too large.",
        "fileError": "Credential file error {path}: {error}",
        "fileInvalid": "the credential file is damaged or has an invalid format",
        "fileUnsupported": "unsupported credential file version",
        "fileUnreadable": "cannot read {path}: {error}",
        "fileWrite": "cannot write {path}: {error}",
        "fileSymlink": "credential file {path} is a symbolic link",
        "fileNotRegular": "credential file {path} is not a regular file",
        "filePermissions": (
            "file {path} is accessible to other users (mode {mode}). "
            "Run: chmod 600 {path}"
        ),
        "fileOwner": "file {path} belongs to another user; run MegaTool as its owner",
        "dirOwner": "directory {path} belongs to another user; run MegaTool as its owner",
        "dirNotDir": "credential path {path} is not a directory",
        "dirPermissions": (
            "directory {path} is accessible to other users (mode {mode}). "
            "Run: chmod 700 {path}"
        ),
        "notConfiguredShort": (
            "Authentication is not configured: start MegaTool in a terminal "
            "and set the administrator password."
        ),
        "badUsername": "username must be 1–64 characters: letters, digits, dot, dash, underscore",
        "passwordShort": "password must be at least {n} characters",
        "passwordLong": "password is too long",
        "passwordMismatch": "passwords do not match",
        "setupTitle": "MegaTool: first-run access setup",
        "setupAccount": "Creating account “{user}” with full access to every section.",
        "setupFailed": "Could not set the password. Run MegaTool again.",
        "passwordNew": "New password: ",
        "passwordRepeat": "Repeat password: ",
        "passwordPrompt": "Password for administrator “{user}”: ",
        "badPassword": "Invalid password.",
        "saved": "Password saved to {path}",
        "created": "Administrator account created: {path}",
        "changeNeedsTty": "Changing the password requires a terminal. Run the command in an interactive console.",
        "statusConfigured": "Authentication is enabled: {user}, file {path}, mode {mode}",
        "statusNotConfigured": "Authentication is not configured. File: {path}",
        "logout": "Sign out",
        "loggedAs": "Signed in as {user}",
        "sessionExpired": "Session expired. Sign in again.",
        "loginTitle": "Sign in to MegaTool",
        "loginSubtitle": "Enter the administrator password to open the console.",
        "loginUser": "Account",
        "loginPassword": "Password",
        "loginButton": "Sign in",
        "loginLoggingIn": "Checking…",
        "loginNotConfigured": "The account is not configured. Run MegaTool in a terminal and set the administrator password.",
        "loginError": "Sign-in failed: {error}",
        "loginHint": "The password is stored in a protected file. A session lasts 12 hours.",
    },
}
