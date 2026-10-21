"""Словарь серверных сообщений Kubernetes."""

DATA = {
    "k8s": {
        "kubectlMissing": "kubectl не найден",
        "kubectlError": "kubectl: ошибка",
        "parseFailed": "не удалось разобрать ответ kubectl",
        "allNamespaces": "все",
        "badPod": "некорректное имя пода",
        "unsupportedResource": "неподдерживаемый ресурс",
        "scaleOnly": "масштабирование поддерживается для deployment/statefulset",
        "rolloutOnly": "перезапуск поддерживается для deployment/statefulset/daemonset",
        "badName": "некорректное имя",
        "deleteDisabled": "удаление ресурсов отключено в настройках",
        "deleteNotAllowed": "удаление этого типа ресурса не разрешено",
        "manifestOutside": "манифест должен лежать внутри рабочего каталога MegaTool",
        "manifestExpected": "ожидается существующий файл .yaml/.yml/.json",
        "defaultContext": "по умолчанию",
        "badContext": "некорректное имя context",
        "badNamespace": "некорректное имя namespace",
    },
}
