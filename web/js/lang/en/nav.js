/* Navigation: section titles and short descriptions (descriptions are tooltips). */

export default {
  top: {
    uptime: "up {duration}",
    noAlerts: "No active alerts",
    lastSample: "last sample: {ago}",
    live: "Live metrics stream",
    liveShort: "live",
    offline: "offline",
    lang: "Switch interface language (RU / EN)",
    theme: "Cycle theme: light → sepia → slate → dark → contrast → auto (Alt+T)",
    tagline: "monitoring and automation",
    menu: "Section menu",
    notifyTitle: "MegaTool: critical alert",
    viewFailed: "Failed to open the section: {error}",
    user: "Current user",
    owner: "MegaTool owner",
    master: "master token",
    local: "local mode",
    logout: "Sign out of MegaTool",
    password: "Change MegaTool password",
    noAccess: "No section is configured for this account. Contact an administrator.",
  },
  nav: {
    groups: {
      monitoring: "Monitoring",
      infrastructure: "Infrastructure",
      automation: "Automation",
      app: "Application",
    },
    dashboard: {
      title: "Overview",
      desc: "Live metrics: CPU, memory, disks, network, processes and alerts",
    },
    checks: {
      title: "Availability",
      desc: "HTTP, TCP and TLS: service state, latency and check history",
    },
    hosts: {
      title: "Servers",
      desc: "Server inventory: availability, uptime, downtime and disabled time",
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
      title: "Apps",
      desc: "Ready-to-deploy app catalog: up and running in a couple of clicks",
    },
    bundles: {
      title: "Config bundles",
      desc: "Sets of files (compose, nginx, Ansible) as one package: create, collect, download",
    },
    configs: {
      title: "Configs",
      desc: "File and config editor: directory tree, highlighting, validation",
    },
    ansible: {
      title: "Ansible",
      desc: "Inventory, playbooks and task runs with a live log",
    },
    servers: {
      title: "SSH servers",
      desc: "Automatic server setup over SSH: checks, package install, deploy bundles",
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
      desc: "Interface, language, alert thresholds, tool paths, access",
    },
    access: {
      title: "Access",
      desc: "Users, permission presets and sign-in with login and password",
    },
  },
};
