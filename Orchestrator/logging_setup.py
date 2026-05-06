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
Metrics (Prometheus-style), JSON log formatter, and logging configuration.
"""

import json
import logging
import sys
import time as _time
import threading as _threading


class Metrics:
    def __init__(self):
        self._lock = _threading.Lock()
        self._counters = {}
        self._gauges = {}
        self._histograms = {}
        self._timers = {}

    def inc(self, name: str, labels: dict = None, value: float = 1):
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def set(self, name: str, value: float, labels: dict = None):
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = value

    def observe(self, name: str, value: float, labels: dict = None):
        key = self._key(name, labels)
        with self._lock:
            if key not in self._histograms:
                self._histograms[key] = []
            self._histograms[key].append(value)

    def start_timer(self, name: str, labels: dict = None) -> str:
        timer_id = self._key(name, labels)
        with self._lock:
            self._timers[timer_id] = _time.monotonic()
        return timer_id

    def stop_timer(self, timer_id: str):
        with self._lock:
            start = self._timers.pop(timer_id, None)
        if start is None:
            return
        elapsed = _time.monotonic() - start
        name = timer_id.split("{")[0] if "{" in timer_id else timer_id
        labels = self._parse_labels(timer_id) if "{" in timer_id else None
        self.observe(name, elapsed, labels)

    def _parse_labels(self, key: str) -> dict:
        if "{" not in key:
            return None
        label_part = key.split("{", 1)[1].rstrip("}")
        labels = {}
        for pair in label_part.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                labels[k.strip()] = v.strip('"')
        return labels

    def _key(self, name, labels):
        if not labels:
            return name
        parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return f'{name}{{{",".join(parts)}}}'

    def render(self) -> str:
        lines = []
        with self._lock:
            seen = set()
            for key, value in sorted(self._counters.items()):
                base = key.split("{")[0] if "{" in key else key
                if base not in seen:
                    lines.append(f"# TYPE {base} counter")
                    seen.add(base)
                lines.append(f"{key} {value}")
            for key, value in sorted(self._gauges.items()):
                base = key.split("{")[0] if "{" in key else key
                if base not in seen:
                    lines.append(f"# TYPE {base} gauge")
                    seen.add(base)
                lines.append(f"{key} {value}")
            for key, values in sorted(self._histograms.items()):
                base = key.split("{")[0] if "{" in key else key
                if base not in seen:
                    lines.append(f"# TYPE {base} summary")
                    seen.add(base)
                sorted_vals = sorted(values)
                for quantile in (0.5, 0.9, 0.99):
                    idx = min(int(len(sorted_vals) * quantile), len(sorted_vals) - 1)
                    lines.append(f'{key}{{quantile="{quantile}"}} {sorted_vals[idx]}')
                lines.append(f"{key}_count {len(values)}")
                lines.append(f"{key}_sum {sum(values)}")
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict:
        result = {"counters": {}, "gauges": {}, "histograms": {}}
        with self._lock:
            for key, value in sorted(self._counters.items()):
                result["counters"][key] = value
            for key, value in sorted(self._gauges.items()):
                result["gauges"][key] = value
            for key, values in sorted(self._histograms.items()):
                sorted_vals = sorted(values)
                result["histograms"][key] = {
                    "count": len(values),
                    "sum": sum(values),
                    "min": sorted_vals[0] if sorted_vals else 0,
                    "max": sorted_vals[-1] if sorted_vals else 0,
                    "p50": sorted_vals[min(int(len(sorted_vals) * 0.5), len(sorted_vals) - 1)] if sorted_vals else 0,
                    "p90": sorted_vals[min(int(len(sorted_vals) * 0.9), len(sorted_vals) - 1)] if sorted_vals else 0,
                    "p99": sorted_vals[min(int(len(sorted_vals) * 0.99), len(sorted_vals) - 1)] if sorted_vals else 0,
                    "avg": sum(values) / len(values) if values else 0,
                }
        return result


metrics = Metrics()


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%S%z", _time.localtime(record.created)),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        for key in ("ticket_id", "agent_id", "pod_name", "event", "correlation_id"):
            val = getattr(record, key, None)
            if val:
                log_entry[key] = val
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


log = logging.getLogger("hivemind")

__all__ = ["Metrics", "JSONFormatter", "setup_logging", "log", "metrics"]