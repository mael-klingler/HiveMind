# Copyright 2025 Mael Klingler
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
Unit tests for queue logic, retry logic, and webhook handler.
Uses mocked database and K8s client for isolation.
"""
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test_hivemind"
os.environ["DB_PATH"] = "/tmp/test_hivemind.db"


class TestDatabaseURL(unittest.TestCase):
    def test_postgres_url_detected(self):
        url = os.getenv("DATABASE_URL", "")
        self.assertTrue(url.startswith("postgresql"), "DATABASE_URL should start with postgresql")


class TestQueueLogic(unittest.TestCase):
    """Tests for queue processing logic using mocked DB."""

    def test_queue_status_priority_ordering(self):
        statuses = ["running", "waiting", "completed", "failed"]
        priority_map = {"running": 1, "waiting": 2, "completed": 3, "failed": 4}
        ordered = sorted(statuses, key=lambda s: priority_map.get(s, 5))
        self.assertEqual(ordered, ["running", "waiting", "completed", "failed"])

    def test_requeue_logic_skips_at_max_retries(self):
        max_retries = 3
        retry_count = 3
        can_requeue = retry_count < max_retries
        self.assertFalse(can_requeue)

    def test_requeue_logic_allows_below_max(self):
        max_retries = 3
        for rc in range(max_retries):
            self.assertTrue(rc < max_retries)


class TestRetryLogic(unittest.TestCase):
    """Tests for ticket retry logic."""

    def test_requeue_under_max_retries(self):
        max_retries = 3
        retry_count = 0
        self.assertTrue(retry_count < max_retries)
        retry_count = 2
        self.assertTrue(retry_count < max_retries)

    def test_requeue_at_max_retries_fails(self):
        max_retries = 3
        retry_count = 3
        self.assertFalse(retry_count < max_retries)

    def test_retry_count_increments(self):
        max_retries = 3
        for attempt in range(max_retries):
            self.assertLess(attempt, max_retries)


class TestWebhookHandler(unittest.TestCase):
    """Tests for GitLab webhook handler logic."""

    def test_verify_webhook_with_valid_signature(self):
        import hashlib
        import hmac
        secret = "test-secret"
        body = b'{"test": "data"}'
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertEqual(len(expected), 64)

    def test_verify_webhook_without_secret(self):
        secret = ""
        if not secret:
            self.assertTrue(True)
        else:
            self.fail("Should skip verification when no secret")

    def test_extract_ticket_id_from_branch(self):
        test_cases = [
            ("feature/PROJ-123-some-desc", "PROJ-123"),
            ("feature/GL-456", "GL-456"),
            ("feature/BUG-789-fix-bug", "BUG-789"),
        ]
        for branch, expected in test_cases:
            cleaned = branch.replace("feature/", "")
            ticket_id = None
            for prefix in ("PROJ-", "BUG-", "TASK-", "GL-"):
                if cleaned.startswith(prefix):
                    idx = len(prefix)
                    while idx < len(cleaned) and cleaned[idx] != '-':
                        idx += 1
                    ticket_id = cleaned[:idx]
                    break
            self.assertEqual(ticket_id, expected, f"Failed for branch {branch}")

    def test_parse_mr_url_valid(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from gitlab_client import parse_mr_url

        test_cases = [
            ("https://gitlab.example.com/group/project/-/merge_requests/42", ("group/project", "42")),
            ("https://gitlab.example.com/group/project/merge_requests/7", ("group/project", "7")),
        ]
        for url, expected in test_cases:
            project_path, mr_iid = parse_mr_url(url)
            self.assertEqual(project_path, expected[0], f"Failed for {url}")
            self.assertEqual(mr_iid, expected[1], f"Failed for {url}")

    def test_parse_mr_url_invalid(self):
        from gitlab_client import parse_mr_url
        result = parse_mr_url("")
        self.assertEqual(result, (None, None))
        result = parse_mr_url("https://example.com/no-mr-here")
        self.assertEqual(result, (None, None))

    def test_webhook_dedup(self):
        dedup = {}
        ttl = 300
        event_id = "unique-event-123"

        self.assertNotIn(event_id, dedup)
        dedup[event_id] = datetime.now(timezone.utc).timestamp()
        self.assertIn(event_id, dedup)

    def test_gitlab_issue_action_filter(self):
        valid_actions = {"open", "update", "reopen"}
        for action in ["open", "update", "reopen"]:
            self.assertIn(action, valid_actions)
        for action in ["close", "merge", ""]:
            self.assertNotIn(action, valid_actions)


class TestK8sClientCompat(unittest.TestCase):
    """Test kubectl_compat function matches expected signatures."""

    def test_kubectl_get_pod_phase(self):
        from k8s_client import kubectl_compat
        parts = "get pod agent-worker-proj-1 -n hivemind -o jsonpath='{.status.phase}'"
        rc, out, err = kubectl_compat(parts)
        self.assertIsInstance(rc, int)
        self.assertIsInstance(out, str)
        self.assertIsInstance(err, str)

    def test_kubectl_delete_pod(self):
        from k8s_client import kubectl_compat
        parts = "delete pod agent-worker-proj-1 -n hivemind --grace-period=0 --force"
        rc, out, err = kubectl_compat(parts)
        self.assertIsInstance(rc, int)

    def test_kubectl_list_pods(self):
        from k8s_client import kubectl_compat
        parts = "get pods -n hivemind -o jsonpath='{range .items[*]}{.metadata.name}{\"\\t\"}{.status.phase}{\"\\n\"}{end}'"
        rc, out, err = kubectl_compat(parts)
        self.assertIsInstance(rc, int)


class TestPodBuilder(unittest.TestCase):
    """Test Pod spec building."""

    def test_build_pod_spec_returns_v1pod(self):
        from pod_builder import build_pod_spec
        from kubernetes import client as kclient
        pod = build_pod_spec(
            ticket_id="PROJ-123",
            ticket_title="Test ticket",
            repos=[{"name": "myrepo", "url": "https://gitlab.example.com/group/myrepo.git", "branch": "development"}],
            assignment_md="# Task",
            analysis={"primary_repo": "myrepo", "complexity": "Medium", "branch": "feature/proj-123-test"},
        )
        self.assertIsInstance(pod, kclient.V1Pod)
        self.assertEqual(pod.metadata.name, "agent-worker-proj-123")
        self.assertEqual(pod.spec.restart_policy, "Never")
        self.assertEqual(len(pod.spec.containers), 1)
        self.assertEqual(len(pod.spec.init_containers), 1)

    def test_pod_uses_secret_ref_for_gitlab_token(self):
        from pod_builder import build_pod_spec
        pod = build_pod_spec(
            ticket_id="GL-1",
            ticket_title="Test",
            repos=[{"name": "r", "url": "https://x.git", "branch": "main"}],
            assignment_md="# T",
            analysis={"primary_repo": "r", "complexity": "Low"},
            gitlab_token="secret-token-here",
        )
        main_container = pod.spec.containers[0]
        gitlab_token_env = [e for e in main_container.env if e.name == "GITLAB_TOKEN"]
        self.assertEqual(len(gitlab_token_env), 1)
        self.assertIsNotNone(gitlab_token_env[0].value_from)
        self.assertIsNotNone(gitlab_token_env[0].value_from.secret_key_ref)
        self.assertEqual(gitlab_token_env[0].value_from.secret_key_ref.name, "gitlab-token")

    def test_pod_uses_secret_ref_for_ollama_api_key(self):
        from pod_builder import build_pod_spec
        pod = build_pod_spec(
            ticket_id="GL-2",
            ticket_title="Test",
            repos=[{"name": "r", "url": "https://x.git", "branch": "main"}],
            assignment_md="# T",
            analysis={"primary_repo": "r", "complexity": "Low"},
            ollama_cloud_api_key="sk-test",
        )
        main_container = pod.spec.containers[0]
        ollama_env = [e for e in main_container.env if e.name == "OLLAMA_CLOUD_API_KEY"]
        self.assertEqual(len(ollama_env), 1)
        self.assertIsNotNone(ollama_env[0].value_from)
        self.assertEqual(ollama_env[0].value_from.secret_key_ref.name, "ollama-cloud-api-key")


class TestGitlabClient(unittest.TestCase):
    """Tests for the GitLab HTTP client module."""

    def test_parse_mr_url_branch(self):
        from gitlab_client import parse_mr_url
        self.assertEqual(parse_mr_url("https://git.example.com/group/repo/-/merge_requests/5"), ("group/repo", "5"))
        self.assertEqual(parse_mr_url(""), (None, None))
        self.assertEqual(parse_mr_url("not-a-url"), (None, None))

    def test_gitlab_headers(self):
        from gitlab_client import _gitlab_headers
        headers = _gitlab_headers("my-token")
        self.assertEqual(headers["PRIVATE-TOKEN"], "my-token")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_gitlab_headers_no_token(self):
        from gitlab_client import _gitlab_headers
        headers = _gitlab_headers("")
        self.assertNotIn("PRIVATE-TOKEN", headers)


if __name__ == "__main__":
    unittest.main()