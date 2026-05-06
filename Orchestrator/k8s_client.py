#!/usr/bin/env python3

# Copyright 2026 Mael Klingler
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Kubernetes client wrapper – replaces all kubectl subprocess calls
with the official kubernetes Python client.
"""

import logging
import os
from typing import Dict, List, Optional, Tuple

from kubernetes import client as kclient
from kubernetes import config as kconfig
from kubernetes.client.exceptions import ApiException
from kubernetes.stream import stream

log = logging.getLogger("hivemind.k8s")

_namespace = os.getenv("AGENT_NAMESPACE", "hivemind")
_k8s_initialized = False


def _init_k8s():
    global _k8s_initialized
    if _k8s_initialized:
        return
    try:
        kconfig.load_incluster_config()
    except kconfig.ConfigException:
        try:
            kconfig.load_kube_config()
        except kconfig.ConfigException:
            log.warning("No K8s config found – kubectl calls will fail")
    _k8s_initialized = True


def get_core_api() -> kclient.CoreV1Api:
    _init_k8s()
    return kclient.CoreV1Api()


def get_pod(name: str, namespace: str = None) -> Optional[kclient.V1Pod]:
    ns = namespace or _namespace
    try:
        return get_core_api().read_namespaced_pod(name=name, namespace=ns)
    except ApiException as e:
        if e.status == 404:
            return None
        raise


def get_pod_phase(name: str, namespace: str = None) -> Optional[str]:
    pod = get_pod(name, namespace)
    if not pod:
        return None
    return pod.status.phase


def list_pods(namespace: str = None, label_selector: str = None) -> List[kclient.V1Pod]:
    ns = namespace or _namespace
    try:
        resp = get_core_api().list_namespaced_pod(namespace=ns, label_selector=label_selector)
        return resp.items
    except ApiException:
        return []


def delete_pod(name: str, namespace: str = None, grace_period: int = 0, force: bool = True):
    ns = namespace or _namespace
    body = kclient.V1DeleteOptions(
        grace_period_seconds=grace_period,
    )
    if force:
        body.kind = "DeleteOptions"
        body.api_version = "v1"
    try:
        get_core_api().delete_namespaced_pod(name=name, namespace=ns, body=body)
        log.info(f"Pod {name} deleted")
    except ApiException as e:
        if e.status != 404:
            log.warning(f"Failed to delete pod {name}: {e}")


def create_pod(pod_body: Dict, namespace: str = None) -> kclient.V1Pod:
    ns = namespace or _namespace
    return get_core_api().create_namespaced_pod(namespace=ns, body=pod_body)


def create_configmap(name: str, data: Dict, labels: Dict = None, namespace: str = None) -> kclient.V1ConfigMap:
    ns = namespace or _namespace
    cm = kclient.V1ConfigMap(
        metadata=kclient.V1ObjectMeta(name=name, namespace=ns, labels=labels or {}),
        data=data,
    )
    return get_core_api().create_namespaced_config_map(namespace=ns, body=cm)


def replace_configmap(name: str, data: Dict, labels: Dict = None, namespace: str = None) -> kclient.V1ConfigMap:
    ns = namespace or _namespace
    cm = kclient.V1ConfigMap(
        metadata=kclient.V1ObjectMeta(name=name, namespace=ns, labels=labels or {}),
        data=data,
    )
    try:
        return get_core_api().replace_namespaced_config_map(name=name, namespace=ns, body=cm)
    except ApiException as e:
        if e.status == 404:
            return create_configmap(name, data, labels, namespace)
        raise


def delete_configmap(name: str, namespace: str = None):
    ns = namespace or _namespace
    try:
        get_core_api().delete_namespaced_config_map(name=name, namespace=ns)
    except ApiException as e:
        if e.status != 404:
            log.warning(f"Failed to delete configmap {name}: {e}")


def cleanup_agent_resources(ticket_id: str):
    namespace = _namespace
    pod_name = f"agent-worker-{ticket_id.lower()}"
    for suffix in ("repos", "assignment", "opencode", "memory"):
        delete_configmap(f"{pod_name}-{suffix}", namespace)
    log.info(f"ConfigMaps cleaned up for {pod_name}")


def get_configmap(name: str, namespace: str = None) -> Optional[kclient.V1ConfigMap]:
    ns = namespace or _namespace
    try:
        return get_core_api().read_namespaced_config_map(name=name, namespace=ns)
    except ApiException as e:
        if e.status == 404:
            return None
        raise


def create_namespaced_secret(name: str, string_data: Dict, namespace: str = None, secret_type: str = "Opaque") -> kclient.V1Secret:
    ns = namespace or _namespace
    secret = kclient.V1Secret(
        metadata=kclient.V1ObjectMeta(name=name, namespace=ns),
        type=secret_type,
        string_data=string_data,
    )
    return get_core_api().create_namespaced_secret(namespace=ns, body=secret)


def get_secret(name: str, namespace: str = None) -> Optional[kclient.V1Secret]:
    ns = namespace or _namespace
    try:
        return get_core_api().read_namespaced_secret(name=name, namespace=ns)
    except ApiException as e:
        if e.status == 404:
            return None
        raise


def ensure_namespace(name: str):
    try:
        get_core_api().read_namespace(name=name)
    except ApiException as e:
        if e.status == 404:
            ns_body = kclient.V1Namespace(metadata=kclient.V1ObjectMeta(name=name))
            get_core_api().create_namespace(body=ns_body)
            log.info(f"Namespace {name} created")


def get_pod_logs(name: str, namespace: str = None, tail_lines: int = 100) -> str:
    ns = namespace or _namespace
    try:
        return get_core_api().read_namespaced_pod_log(
            name=name, namespace=ns, tail_lines=tail_lines
        )
    except ApiException as e:
        if e.status == 404:
            return ""
        raise


def get_pod_ip(name: str, namespace: str = None) -> Optional[str]:
    pod = get_pod(name, namespace)
    if pod and pod.status and pod.status.pod_ip:
        return pod.status.pod_ip
    return None


def kubectl_compat(args: str) -> Tuple[int, str, str]:
    """Backward-compatible _kubectl replacement.

    Accepts the same args string format used by the old subprocess calls
    and dispatches to the Python client. Returns (rc, stdout, stderr).
    """
    parts = args.strip().split()
    cmd = parts[0] if parts else ""

    try:
        if cmd == "get":
            return _handle_get(parts[1:])
        elif cmd == "delete":
            return _handle_delete(parts[1:])
        elif cmd == "apply":
            return _handle_apply(parts[1:])
        elif cmd == "create":
            return _handle_create(parts[1:])
        elif cmd == "logs":
            return _handle_logs(parts[1:])
        else:
            return 1, "", f"Unsupported kubectl command: {cmd}"
    except ApiException as e:
        return e.status, "", e.body or str(e)
    except Exception as e:
        return 1, "", str(e)


def _parse_namespace_and_name(parts: List[str]) -> Tuple[str, str]:
    ns = _namespace
    name = ""
    i = 0
    while i < len(parts):
        if parts[i] == "-n" and i + 1 < len(parts):
            ns = parts[i + 1]
            i += 2
        elif parts[i] == "pod" and i + 1 < len(parts):
            name = parts[i + 1]
            i += 2
        elif parts[i] == "configmap" and i + 1 < len(parts):
            name = parts[i + 1]
            i += 2
        elif parts[i] == "secret" and i + 1 < len(parts):
            name = parts[i + 1]
            i += 2
        elif parts[i] == "namespace" and i + 1 < len(parts):
            name = parts[i + 1]
            i += 2
        else:
            name = name or parts[i]
            i += 1
    return ns, name


def _handle_get(parts: List[str]) -> Tuple[int, str, str]:
    resource = parts[0] if parts else ""
    if resource == "pods":
        if "-o" in parts and "jsonpath" in " ".join(parts):
            idx = parts.index("-o")
            jsonpath_expr = parts[idx + 1].strip("'\"")
            ns, name = _parse_namespace_and_name(parts[1:idx])
            if name:
                pod = get_pod(name, ns)
                if not pod:
                    return 1, "", f"Pod {name} not found"
                return _extract_jsonpath(pod, jsonpath_expr)
            label_sel = None
            for i, p in enumerate(parts):
                if p == "-l" and i + 1 < len(parts):
                    label_sel = parts[i + 1]
            pods = list_pods(ns, label_sel)
            rows = []
            for p in pods:
                rows.append(f"{p.metadata.name}\t{p.status.phase}")
            return 0, "\n".join(rows), ""
        ns, name = _parse_namespace_and_name(parts[1:])
        if name:
            pod = get_pod(name, ns)
            if pod:
                return 0, pod.metadata.name, ""
            return 1, "", "Not found"
        return 1, "", "Missing resource name"

    elif resource == "namespace":
        ns_name = parts[1] if len(parts) > 1 else ""
        try:
            get_core_api().read_namespace(name=ns_name)
            return 0, ns_name, ""
        except ApiException as e:
            if e.status == 404:
                return 1, "", "NotFound"
            return e.status, "", str(e)

    elif resource == "configmap":
        ns, name = _parse_namespace_and_name(parts[1:])
        cm = get_configmap(name, ns)
        if cm:
            return 0, name, ""
        return 1, "", "NotFound"

    elif resource == "secret":
        ns, name = _parse_namespace_and_name(parts[1:])
        sec = get_secret(name, ns)
        if sec:
            return 0, name, ""
        return 1, "", "NotFound"

    return 1, "", f"Unsupported get resource: {resource}"


def _handle_delete(parts: List[str]) -> Tuple[int, str, str]:
    resource = parts[0] if parts else ""
    ns, name = _parse_namespace_and_name(parts[1:])

    force = "--force" in parts
    grace_period = 0
    if "--grace-period=0" in parts:
        grace_period = 0

    if resource == "pod" and name:
        try:
            delete_pod(name, ns, grace_period=grace_period, force=force)
            return 0, f"pod {name} deleted", ""
        except ApiException as e:
            if e.status == 404:
                return 0, "", ""
            return e.status, "", str(e)
    elif resource == "configmap" and name:
        try:
            delete_configmap(name, ns)
            return 0, f"configmap {name} deleted", ""
        except ApiException as e:
            return e.status, "", str(e)

    return 1, "", f"Unsupported delete resource: {resource}"


def _handle_apply(parts: List[str]) -> Tuple[int, str, str]:
    if "-f" in parts:
        idx = parts.index("-f")
        filepath = parts[idx + 1] if idx + 1 < len(parts) else ""
        import yaml
        with open(filepath, "r") as f:
            docs = list(yaml.safe_load_all(f))
        for doc in docs:
            if not doc:
                continue
            kind = doc.get("kind", "")
            _apply_resource(doc, kind)
        return 0, "applied", ""
    return 1, "", "apply requires -f"


def _handle_create(parts: List[str]) -> Tuple[int, str, str]:
    if "namespace" in parts:
        ns_name = parts[-1] if parts else ""
        ensure_namespace(ns_name)
        return 0, f"namespace {ns_name} created", ""
    return 1, "", "Unsupported create command"


def _handle_logs(parts: List[str]) -> Tuple[int, str, str]:
    ns, name = _parse_namespace_and_name(parts)
    tail = 100
    if "--tail" in parts:
        idx = parts.index("--tail")
        tail = int(parts[idx + 1]) if idx + 1 < len(parts) else 100
    if name:
        logs = get_pod_logs(name, ns, tail_lines=tail)
        return 0, logs, ""
    return 1, "", "Missing pod name"


def _apply_resource(doc: Dict, kind: str):
    meta = doc.get("metadata", {})
    name = meta.get("name", "")
    ns = meta.get("namespace", _namespace)
    api = get_core_api()

    if kind == "Pod":
        try:
            api.create_namespaced_pod(namespace=ns, body=doc)
        except ApiException as e:
            if e.status == 409:
                log.warning(f"Pod {name} already exists, replacing")
                api.delete_namespaced_pod(name=name, namespace=ns, body=kclient.V1DeleteOptions(grace_period_seconds=0))
                import time
                time.sleep(2)
                api.create_namespaced_pod(namespace=ns, body=doc)
            else:
                raise
    elif kind == "ConfigMap":
        try:
            api.create_namespaced_config_map(namespace=ns, body=doc)
        except ApiException as e:
            if e.status == 409:
                api.replace_namespaced_config_map(name=name, namespace=ns, body=doc)
            else:
                raise
    elif kind == "Secret":
        try:
            api.create_namespaced_secret(namespace=ns, body=doc)
        except ApiException as e:
            if e.status == 409:
                api.replace_namespaced_secret(name=name, namespace=ns, body=doc)
            else:
                raise


def _extract_jsonpath(pod: kclient.V1Pod, expr: str) -> Tuple[int, str, str]:
    expr_clean = expr.strip("'\"")
    if "{.status.phase}" in expr_clean:
        return 0, pod.status.phase or "", ""
    if "{.status.podIP}" in expr_clean:
        return 0, pod.status.pod_ip or "", ""
    if "range .items[*]}" in expr_clean:
        return 0, f"{pod.metadata.name}\t{pod.status.phase}", ""
    if "{.metadata.name}" in expr_clean:
        return 0, pod.metadata.name, ""
    return 0, str(pod.status.phase), ""