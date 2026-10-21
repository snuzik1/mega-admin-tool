"""English texts of the remote setup task catalog (megatool/provision.py)."""

DATA = {
    "tasks": {
        "groups": {
            "base": "Basics",
            "system": "System",
            "user": "Users",
            "security": "Security",
            "docker": "Docker",
            "web": "Web server",
        },
        "risk": {
            "safe": "Safe",
            "medium": "Medium risk",
            "high": "High risk",
        },
        "base-update": {
            "title": "Refresh package index",
            "description": "Refreshes the list of available packages (apt/dnf/yum/pacman/zypper). Does not upgrade packages.",
        },
        "base-tools": {
            "title": "Basic utilities",
            "description": "Installs curl, git, htop, vim, tmux, unzip, jq and ca-certificates.",
            "params": {
                "extra": {
                    "label": "Extra packages",
                    "hint": "Space-separated; optional.",
                },
            },
        },
        "system-tz": {
            "title": "Time zone",
            "description": "Sets the system time zone via timedatectl (or /etc/localtime).",
            "params": {
                "tz": {
                    "label": "Time zone",
                    "hint": "For example: Europe/Moscow, UTC.",
                },
            },
        },
        "system-swap": {
            "title": "Swap file",
            "description": "Creates (or enables) a /swapfile of the given size and adds it to /etc/fstab.",
            "params": {
                "size_gb": {
                    "label": "Size, GB",
                    "hint": "From 1 to 64 GB.",
                },
            },
        },
        "user-create": {
            "title": "Create user",
            "description": "Creates a user with a home directory and optionally installs an SSH key.",
            "params": {
                "user": {
                    "label": "User name",
                    "hint": "Lowercase latin letters, digits, dash and underscore.",
                },
                "sshkey": {
                    "label": "Public SSH key",
                    "hint": "Starts with ssh-ed25519, ssh-rsa or ecdsa-sha2-; optional.",
                },
            },
        },
        "security-ssh": {
            "title": "Harden SSH",
            "description": "Disables password and root login, leaving keys only. Make sure key login works first!",
        },
        "security-fw": {
            "title": "Configure firewall",
            "description": "Opens ports 22, 80 and 443 in ufw or firewalld.",
            "params": {
                "backend": {
                    "label": "Implementation",
                    "hint": "auto — detect from installed packages.",
                    "choices": {
                        "auto": "detect automatically",
                    },
                },
            },
        },
        "docker-install": {
            "title": "Install Docker",
            "description": "Installs Docker from the official get.docker.com script and adds a user to the docker group.",
            "params": {
                "user": {
                    "label": "User for the docker group",
                    "hint": "Optional; the user must log in again afterwards.",
                },
            },
        },
        "web-nginx": {
            "title": "Install nginx",
            "description": "Installs nginx, enables it at boot and starts the service.",
        },
        "security-fail2ban": {
            "title": "Install fail2ban",
            "description": "Installs fail2ban, enables it at boot and starts the service.",
        },
    },
}
