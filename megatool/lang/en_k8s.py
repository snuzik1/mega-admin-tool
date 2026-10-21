"""Kubernetes messages."""

DATA = {
    "k8s": {
        "kubectlMissing": "kubectl not found",
        "kubectlError": "kubectl: error",
        "parseFailed": "failed to parse the kubectl response",
        "allNamespaces": "all",
        "badPod": "invalid pod name",
        "unsupportedResource": "unsupported resource",
        "scaleOnly": "scaling is supported for deployment/statefulset",
        "rolloutOnly": "restart is supported for deployment/statefulset/daemonset",
        "badName": "invalid name",
        "deleteDisabled": "resource deletion is disabled in settings",
        "deleteNotAllowed": "deleting this resource type is not allowed",
        "manifestOutside": "the manifest must be inside the MegaTool working directory",
        "manifestExpected": "an existing .yaml/.yml/.json file is expected",
        "defaultContext": "default",
        "badContext": "invalid context name",
        "badNamespace": "invalid namespace name",
    },
}
