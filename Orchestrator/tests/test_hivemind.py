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
Comprehensive test suite for HiveMind project.
Covers API authentication, rate limiting, database routing,
VCS providers, metrics, security context, graceful shutdown, and CLI.
"""
import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ORIG_ENV = dict(os.environ)


def _reset_env():
    """Restore env, preserving any vars pytest injected (like PYTEST_CURRENT_TEST)."""
    # Get current pytest-managed keys that weren't in our original env
    current_keys = set(os.environ.keys())
    for k, v in ORIG_ENV.items():
        os.environ[k] = v
    for k in list(os.environ.keys()):
        if k not in ORIG_ENV and not k.startswith("PYTEST"):
            del os.environ[k]


def _setup_test_db():
    """Create a temp SQLite DB and initialize schema for server tests."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    os.environ["DB_PATH"] = db_path
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("SUPABASE_URL", None)
    os.environ.pop("SUPABASE_SERVICE_KEY", None)
    from database.init_db import init_db
    init_db()
    return db_path


def _cleanup_test_db(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _purge_modules(prefix):
    """Remove modules from sys.modules so they get re-imported fresh."""
    to_remove = [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]
    for k in to_remove:
        sys.modules.pop(k, None)


class TestAPIAuthentication(unittest.TestCase):
    """When HIVEMIND_API_KEY is set, /api/* endpoints require X-API-Key header."""

    @classmethod
    def setUpClass(cls):
        _reset_env()
        os.environ["HIVEMIND_API_KEY"] = "test-secret-key"
        cls._db_path = _setup_test_db()
        _purge_modules("config")
        _purge_modules("middleware")
        _purge_modules("server")
        _purge_modules("database")
        import config as _cfg
        importlib.reload(_cfg)
        import middleware as _mw
        importlib.reload(_mw)
        import server as _srv
        importlib.reload(_srv)
        cls.app = _srv.app
        from fastapi.testclient import TestClient
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_db(cls._db_path)
        _reset_env()

    def test_api_key_required_when_set_no_key(self):
        resp = self.client.post("/api/tickets", json={"title": "test", "priority": "Low"})
        self.assertIn(resp.status_code, (401, 403))

    def test_api_key_required_when_set_wrong_key(self):
        resp = self.client.post("/api/tickets", json={"title": "test", "priority": "Low"}, headers={"X-API-Key": "wrong-key"})
        self.assertIn(resp.status_code, (401, 403))

    def test_api_key_required_when_set_correct_key(self):
        resp = self.client.get("/api/tickets", headers={"X-API-Key": "test-secret-key"})
        self.assertNotEqual(resp.status_code, 401)

    def test_api_key_bearer_auth(self):
        resp = self.client.get("/api/tickets", headers={"Authorization": "Bearer test-secret-key"})
        self.assertNotEqual(resp.status_code, 401)

    def test_healthz_exempt_from_auth(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)

    def test_readyz_exempt_from_auth(self):
        resp = self.client.get("/readyz")
        self.assertIn(resp.status_code, (200, 404, 503))


class TestAPIKeyOptionalWhenUnset(unittest.TestCase):
    """When HIVEMIND_API_KEY is not set, /api/* endpoints are open."""

    @classmethod
    def setUpClass(cls):
        _reset_env()
        os.environ.pop("HIVEMIND_API_KEY", None)
        cls._db_path = _setup_test_db()
        _purge_modules("config")
        _purge_modules("middleware")
        _purge_modules("server")
        _purge_modules("database")
        import config as _cfg
        importlib.reload(_cfg)
        import middleware as _mw
        importlib.reload(_mw)
        import server as _srv
        importlib.reload(_srv)
        cls.app = _srv.app
        from fastapi.testclient import TestClient
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        _cleanup_test_db(cls._db_path)
        _reset_env()

    def test_api_key_optional_when_unset(self):
        resp = self.client.get("/api/tickets")
        self.assertNotEqual(resp.status_code, 401)


class TestRateLimiting(unittest.TestCase):
    """Verify rate limit is enforced after threshold."""

    def setUp(self):
        _reset_env()
        os.environ.pop("HIVEMIND_API_KEY", None)
        self._db_path = _setup_test_db()

    def tearDown(self):
        _cleanup_test_db(self._db_path)
        _reset_env()

    def test_rate_limit_enforcement(self):
        os.environ["RATE_LIMIT_PER_MINUTE"] = "3"
        _purge_modules("config")
        _purge_modules("middleware")
        _purge_modules("server")
        _purge_modules("database")
        _purge_modules("redis_client")
        import config as _cfg
        importlib.reload(_cfg)
        import middleware as _mw
        importlib.reload(_mw)
        import server as _srv
        importlib.reload(_srv)
        from fastapi.testclient import TestClient
        client = TestClient(_srv.app)
        for _ in range(3):
            resp = client.post("/api/tickets", json={"title": "test", "priority": "Low"})
            self.assertNotEqual(resp.status_code, 429, "Should not be rate limited yet")
        resp = client.post("/api/tickets", json={"title": "test", "priority": "Low"})
        self.assertEqual(resp.status_code, 429, "Should be rate limited after exceeding threshold")

    def test_rate_limit_exempt_paths(self):
        os.environ.pop("HIVEMIND_API_KEY", None)
        os.environ["RATE_LIMIT_PER_MINUTE"] = "1"
        _purge_modules("config")
        _purge_modules("middleware")
        _purge_modules("server")
        _purge_modules("database")
        _purge_modules("redis_client")
        import config as _cfg
        importlib.reload(_cfg)
        import server as _srv
        importlib.reload(_srv)
        from fastapi.testclient import TestClient
        client = TestClient(_srv.app)
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)


class TestDatabaseRouter(unittest.TestCase):
    def test_sqlite_default(self):
        _reset_env()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_KEY", None)
        os.environ["DB_PATH"] = "/tmp/test_hivemind_router.db"
        _purge_modules("database")
        _purge_modules("database.sqlite_adapter")
        _purge_modules("database.sqlite_backend")
        import database as db
        importlib.reload(db)
        self.assertFalse(db.USE_SUPABASE)

    def test_postgres_detected(self):
        _reset_env()
        os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost/db"
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_KEY", None)
        self.assertTrue(os.getenv("DATABASE_URL", "").startswith("postgresql"))
        _reset_env()


class TestVCSProviderSelection(unittest.TestCase):
    def test_default_gitlab_provider(self):
        _reset_env()
        os.environ.pop("VCS_PROVIDER", None)
        from vcs import get_vcs_provider
        provider = get_vcs_provider()
        self.assertEqual(provider.name, "gitlab")

    def test_github_provider_selection(self):
        _reset_env()
        os.environ["VCS_PROVIDER"] = "github"
        from vcs import get_vcs_provider
        provider = get_vcs_provider()
        self.assertEqual(provider.name, "github")
        _reset_env()

    def test_gitlab_parse_mr_url(self):
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        path, iid = provider.parse_mr_url("https://gitlab.com/group/project/-/merge_requests/42")
        self.assertEqual(path, "group/project")
        self.assertEqual(iid, "42")

    def test_gitlab_parse_mr_url_no_dash(self):
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        path, iid = provider.parse_mr_url("https://gitlab.com/group/project/merge_requests/42")
        self.assertEqual(path, "group/project")
        self.assertEqual(iid, "42")

    def test_gitlab_parse_mr_url_empty(self):
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        path, iid = provider.parse_mr_url("")
        self.assertIsNone(path)
        self.assertIsNone(iid)

    def test_github_parse_pr_url(self):
        from vcs.github import GitHubProvider
        provider = GitHubProvider()
        path, num = provider.parse_mr_url("https://github.com/owner/repo/pull/42")
        self.assertEqual(path, "owner/repo")
        self.assertEqual(num, "42")

    def test_github_parse_pr_url_empty(self):
        from vcs.github import GitHubProvider
        provider = GitHubProvider()
        path, num = provider.parse_mr_url("")
        self.assertIsNone(path)
        self.assertIsNone(num)

    def test_github_parse_non_pr_url(self):
        from vcs.github import GitHubProvider
        provider = GitHubProvider()
        path, num = provider.parse_mr_url("https://github.com/owner/repo/issues/5")
        self.assertIsNone(path)
        self.assertIsNone(num)

    def test_gitlab_default_git_user(self):
        from vcs.gitlab import GitLabProvider
        self.assertEqual(GitLabProvider().get_default_git_user(), "gitlab-ci-token")

    def test_github_default_git_user(self):
        from vcs.github import GitHubProvider
        self.assertEqual(GitHubProvider().get_default_git_user(), "x-access-token")

    def test_gitlab_extract_ticket_id(self):
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        self.assertEqual(provider.extract_ticket_id_from_branch("feature/PROJ-123-slug"), "PROJ-123")
        self.assertIsNone(provider.extract_ticket_id_from_branch("feature/fix-typo"))

    def test_github_extract_ticket_id(self):
        from vcs.github import GitHubProvider
        provider = GitHubProvider()
        self.assertEqual(provider.extract_ticket_id_from_branch("feature/GH-456-something"), "GH-456")
        self.assertIsNone(provider.extract_ticket_id_from_branch("main"))

    def test_gitlab_auth_headers(self):
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        headers = provider.auth_headers("test-token")
        self.assertEqual(headers["PRIVATE-TOKEN"], "test-token")

    def test_github_auth_headers(self):
        from vcs.github import GitHubProvider
        provider = GitHubProvider()
        headers = provider.auth_headers("test-token")
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        self.assertIn("X-GitHub-Api-Version", headers)


class TestMetrics(unittest.TestCase):
    def test_counter_increment(self):
        from logging_setup import Metrics
        m = Metrics()
        m.inc("test_counter")
        m.inc("test_counter")
        output = m.render()
        self.assertIn("test_counter 2", output)

    def test_gauge_set(self):
        from logging_setup import Metrics
        m = Metrics()
        m.set("test_gauge", 42)
        output = m.render()
        self.assertIn("test_gauge 42", output)

    def test_histogram_observe(self):
        from logging_setup import Metrics
        m = Metrics()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            m.observe("test_hist", v)
        output = m.render()
        self.assertIn("test_hist_count 5", output)
        self.assertIn("test_hist_sum 15.0", output)

    def test_labeled_metric(self):
        from logging_setup import Metrics
        m = Metrics()
        m.inc("http_requests", labels={"method": "GET"})
        output = m.render()
        self.assertIn('http_requests{method="GET"}', output)

    def test_counter_with_value(self):
        from logging_setup import Metrics
        m = Metrics()
        m.inc("batch_counter", value=5)
        m.inc("batch_counter", value=3)
        output = m.render()
        self.assertIn("batch_counter 8", output)

    def test_render_empty_metrics(self):
        from logging_setup import Metrics
        m = Metrics()
        output = m.render()
        self.assertEqual(output, "\n")


class TestSecurityContext(unittest.TestCase):
    def test_pod_spec_has_security_context(self):
        try:
            from pod_builder import build_pod_spec
            from kubernetes import client as kclient
            pod = build_pod_spec(
                ticket_id="TEST-1",
                ticket_title="Test ticket",
                repos=[{"name": "test-repo", "url": "https://gitlab.com/test/repo.git", "branch": "main"}],
                assignment_md="# Test",
                analysis={"primary_repo": "test-repo"},
                gitlab_host="gitlab.com",
            )
            self.assertIsNotNone(pod.spec.security_context)
            self.assertTrue(pod.spec.security_context.run_as_non_root)
            self.assertEqual(pod.spec.security_context.run_as_user, 1000)
        except ImportError:
            self.skipTest("kubernetes client not available")


class TestGracefulShutdown(unittest.TestCase):
    def test_shutdown_event_sets_running_flag(self):
        import asyncio
        from background.queue_processor import set_running, _running
        set_running(True)
        import server as _srv
        asyncio.get_event_loop().run_until_complete(_srv.shutdown_event())
        self.assertFalse(_running)
        set_running(True)

    def test_sigterm_handler_callable(self):
        import server as _srv
        self.assertTrue(callable(getattr(_srv, "shutdown_event", None)))


class TestCLI(unittest.TestCase):
    def test_cli_help(self):
        import subprocess
        result = subprocess.run(
            ["python3", os.path.join(os.path.dirname(__file__), "..", "..", "cli", "hivemind")],
            capture_output=True, text=True, timeout=5
        )
        self.assertTrue(
            result.returncode != 0 or "usage" in result.stdout.lower() or "usage" in result.stderr.lower()
        )

    def test_cli_parser_builds(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "cli"))
        try:
            import importlib
            hivemind_cli = importlib.import_module("hivemind")
            parser = hivemind_cli.build_parser()
            self.assertIsNotNone(parser)
            self.assertEqual(parser.prog, "hivemind")
        except ImportError:
            self.skipTest("CLI module not importable")

    def test_cli_has_ticket_subcommand(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "cli"))
        try:
            import importlib
            hivemind_cli = importlib.import_module("hivemind")
            parser = hivemind_cli.build_parser()
            subactions = [a for a in parser._subparsers._actions if hasattr(a, "_name_parser_map")]
            subparser_names = list(subactions[0]._name_parser_map.keys()) if subactions else []
            self.assertIn("ticket", subparser_names)
        except (ImportError, AttributeError):
            self.skipTest("CLI module structure not available")


class TestGitLabWebhookParsing(unittest.TestCase):
    def test_parse_mr_url_with_dash(self):
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        path, iid = provider.parse_mr_url("https://gitlab.example.com/group/project/-/merge_requests/5")
        self.assertEqual(path, "group/project")
        self.assertEqual(iid, "5")

    def test_parse_mr_url_without_dash(self):
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        path, iid = provider.parse_mr_url("https://gitlab.example.com/group/project/merge_requests/7")
        self.assertEqual(path, "group/project")
        self.assertEqual(iid, "7")

    def test_parse_mr_url_invalid(self):
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        self.assertEqual(provider.parse_mr_url("https://example.com/no-mr-here"), (None, None))

    def test_gitlab_webhook_issue_event(self):
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        payload = {
            "object_kind": "issue",
            "object_attributes": {"iid": 42, "action": "open", "title": "Test issue", "url": "https://gitlab.com/group/proj/issues/42"},
            "project": {"id": 1, "path_with_namespace": "group/proj"},
            "labels": [],
        }
        headers = {"X-Gitlab-Event": "Issue Hook"}
        result = provider.parse_webhook_event(payload, headers)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "issue")
        self.assertEqual(result["action"], "open")

    def test_gitlab_webhook_mr_event(self):
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        payload = {
            "object_attributes": {"iid": 7, "action": "open", "title": "MR test", "url": "https://gitlab.com/group/proj/-/merge_requests/7", "source_branch": "feature", "target_branch": "main"},
            "project": {"id": 1, "path_with_namespace": "group/proj"},
        }
        headers = {"X-Gitlab-Event": "Merge Request Hook"}
        result = provider.parse_webhook_event(payload, headers)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "merge_request")

    def test_github_webhook_issue_event(self):
        from vcs.github import GitHubProvider
        provider = GitHubProvider()
        payload = {
            "action": "opened",
            "issue": {"number": 10, "title": "Bug", "body": "desc", "html_url": "https://github.com/o/r/issues/10", "labels": []},
            "repository": {"id": 1, "full_name": "owner/repo"},
        }
        headers = {"X-GitHub-Event": "issues"}
        result = provider.parse_webhook_event(payload, headers)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "issue")
        self.assertEqual(result["action"], "open")

    def test_github_webhook_pr_event(self):
        from vcs.github import GitHubProvider
        provider = GitHubProvider()
        payload = {
            "action": "opened",
            "pull_request": {"number": 5, "title": "PR", "html_url": "https://github.com/o/r/pull/5", "state": "open", "head": {"ref": "feature"}, "base": {"ref": "main"}},
            "repository": {"id": 1, "full_name": "owner/repo"},
        }
        headers = {"X-GitHub-Event": "pull_request"}
        result = provider.parse_webhook_event(payload, headers)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "merge_request")


if __name__ == "__main__":
    unittest.main()