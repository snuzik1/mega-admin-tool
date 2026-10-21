"""English messages for the Ansible module."""

DATA = {
    "ansible": {
        "notFound": "Ansible not found. Install it: pip install ansible (or set the path in settings)",
        "inventoryNotFound": "inventory not found",
        "inventorySourceIni": "INI file (local parser)",
        "playbookNotFound": "playbook not found: {playbook}",
        "playbookBinNotFound": "ansible-playbook not found — install Ansible or set the path in settings",
        "playbookBinMissing": "ansible-playbook not found",
        "badLimit": "invalid --limit (allowed: letters, digits, , : & ! * [ ])",
        "badTags": "invalid tags",
        "badSkipTags": "invalid --skip-tags",
        "jobTitle": "Ansible: {name}",
        "jobLimit": " (limit: {limit})",
        "adhocTitle": "Ansible ad-hoc: {module} → {pattern}",
        "binNotFound": "ansible not found — install Ansible or set the path in settings",
        "badModule": "invalid module name",
        "badPattern": "invalid host pattern",
    },
}
