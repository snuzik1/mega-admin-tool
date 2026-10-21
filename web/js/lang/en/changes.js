/* Changes section strings (English). */

export default {
  changes: {
    title: "Changes",
    subtitle: "What changed on the system: files, packages, logins, services, logs and the MegaTool audit trail",
    loading: "Collecting the change report…",
    loadFailed: "Failed to fetch the change report",
    reportUnavailable: "Report unavailable",
    sectionsUnavailable: "Sections are not available on this system",
    sectionUnavailable: "Data source is not available on this system",
    summary: "Summary for {range}",
    hours: "{n} h",
    generated: "Report generated at {time}. The cache refreshes automatically, at most once a minute.",
    sourceUnavailable: "source unavailable",
    range: {
      h1: "1 hour",
      h6: "6 hours",
      h24: "24 hours",
      d3: "3 days",
      d7: "7 days",
    },
    files: {
      filter: "filter by path…",
      scanned: "directories scanned: {n}",
      modified: "Modified",
      path: "File",
      root: "Root",
      empty: "no modified files",
      emptyFiltered: "nothing found for the filter",
    },
    col: {
      size: "Size",
      time: "Time",
      kind: "Kind",
      event: "Event",
      detail: "Details",
    },
    empty: {
      packages: "no package changes found",
      logins: "no login records",
      services: "no problematic services",
      logs: "no log errors found",
      audit: "no MegaTool actions in this period",
    },
  },
};
