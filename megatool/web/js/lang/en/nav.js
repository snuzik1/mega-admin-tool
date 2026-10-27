/* Navigation and help: section titles, short descriptions and the help dialog. */

export default {
  top: {
    uptime: "up {duration}",
    noAlerts: "No active alerts",
    alerts: "Alerts: {n}",
    lastSample: "last sample: {ago}",
    live: "Live metrics stream",
    liveShort: "live",
    offline: "offline",
    lang: "Switch interface language (RU / EN)",
    theme: "Cycle theme: light → sepia → slate → dark → contrast → auto (Alt+T)",
    tagline: "monitoring and automation",
    menu: "Section menu",
    help: "Interface help (F1)",
    tools: "Tools",
    toolsHint: "Availability of Docker, Podman, kubectl, Ansible and SSH",
    toolsTitle: "External tools",
    toolsConfigure: "Configure",
    toolsSettings: "Tool settings",
    toolsReady: "ready",
    toolsFound: "available",
    toolsMissing: "not found",
    toolsDaemon: "daemon not responding",
    toolsNote: "If a tool is missing, install it or set its path in Settings → Tools.",
    environment: "Environment",
    environmentHint: "Environment limitations and working directory",
    notifyTitle: "MegaTool: critical alert",
    viewFailed: "Failed to open the section: {error}",
  },
  nav: {
    groups: {
      monitoring: "Monitoring",
      infrastructure: "Infrastructure",
      automation: "Automation",
      app: "System",
    },
    dashboard: {
      title: "Overview",
      desc: "Live metrics: CPU, memory, disks, network, processes and alerts",
    },
    checks: {
      title: "Service checks",
      desc: "HTTP, TCP and TLS: service state, latency and check history",
    },
    hosts: {
      title: "Servers",
      desc: "Server inventory and availability monitoring: uptime, downtime and disabled time",
    },
    containers: {
      title: "Containers",
      desc: "Docker and Podman: state, logs, start/stop, cleanup",
    },
    balancer: {
      title: "Load balancer",
      desc: "Balancing proxy: targets, strategies, health checks, nginx/haproxy export",
    },
    catalog: {
      title: "App catalog",
      desc: "Ready recipes: Postgres, Redis, Nginx, monitoring, k8s manifests — deploy in a couple of clicks",
    },
    bundles: {
      title: "File bundles",
      desc: "Named file sets (compose, nginx, Ansible) as one package: create, collect, download",
    },
    configs: {
      title: "Files & configs",
      desc: "Workspace editor: file tree, highlighting, syntax validation",
    },
    ansible: {
      title: "Ansible",
      desc: "Inventory, playbooks and task runs with a live log",
    },
    servers: {
      title: "SSH setup",
      desc: "Remote server setup over SSH: checks, package install, task runs",
    },
    k8s: {
      title: "Kubernetes",
      desc: "Whole cluster: nodes, pods, deployments, services, events, manifest apply",
    },
    changes: {
      title: "Changes",
      desc: "What changed on the system: files, packages, services, logs and audit trail",
    },
    jobs: {
      title: "Jobs",
      desc: "Running and past jobs with full output logs",
    },
    settings: {
      title: "Settings",
      desc: "Interface, language, alert thresholds and tool paths",
    },
  },
  help: {
    title: "Interface help",
    lead: "MegaTool is a self-hosted console for monitoring and automation. Sections are grouped by task; hover a menu item to see its description.",
    startTitle: "Getting started",
    start: {
      overview: "“Overview” shows live metrics for this machine and active alerts.",
      checks: "“Service checks” tracks HTTP, TCP and TLS endpoints you care about.",
      hosts: "“Servers” is the machine inventory and availability view, including uptime and downtime.",
      tools: "“Tools” in the top bar shows which external commands are available; set their paths in settings.",
    },
    groupsTitle: "Menu groups",
    groups: {
      monitoring: "Observation: overview, service checks, servers, changes and jobs.",
      infrastructure: "Resources: containers, Kubernetes, load balancer and ready-made apps.",
      automation: "Files and servers: file bundles, editor, Ansible and SSH setup.",
      app: "MegaTool parameters: server, monitoring, tools, safety and interface.",
    },
    termsTitle: "Similar sections",
    terms: {
      checks: "Service checks are external HTTP/TCP/TLS endpoints — not the availability of inventory servers.",
      hosts: "Servers is the list of monitored machines: state, uptime, downtime and check history.",
      servers: "SSH setup installs packages and runs tasks on remote servers.",
      catalog: "App catalog contains ready recipes (compose, k8s, configs) for deployment in a couple of clicks.",
      bundles: "File bundles are your own named file sets for transfer and download.",
      configs: "Files & configs is the MegaTool workspace editor with syntax validation.",
    },
    shortcutsTitle: "Keyboard shortcuts",
    shortcuts: {
      theme: "Cycle theme",
      help: "Open this help",
      escape: "Close a dialog or menu",
    },
  },
};
