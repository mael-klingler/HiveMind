"""
Tests for _resolve_pod_url: verifies that pods remain reachable
even when the ticket status is not 'running' or 'queued'.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["DB_PATH"] = "/tmp/test_hivemind_resolve_pod.db"
os.environ.setdefault("AGENT_NAMESPACE", "hivemind")


class TestResolvePodUrl(unittest.TestCase):

    @patch("k8s_client.get_pod_phase")
    @patch("k8s_client.get_pod_ip")
    @patch("database.get_ticket")
    def test_running_ticket_resolves(self, mock_get_ticket, mock_get_pod_ip, mock_get_pod_phase):
        mock_get_ticket.return_value = {"id": "TASK-1", "status": "running"}
        mock_get_pod_ip.return_value = "10.0.0.5"
        from api.proxy import _resolve_pod_url
        result = _resolve_pod_url("TASK-1")
        self.assertIsNotNone(result)
        self.assertIn("10.0.0.5", result)

    @patch("k8s_client.get_pod_phase")
    @patch("k8s_client.get_pod_ip")
    @patch("database.get_ticket")
    def test_completed_ticket_still_resolves(self, mock_get_ticket, mock_get_pod_ip, mock_get_pod_phase):
        mock_get_ticket.return_value = {"id": "TASK-1", "status": "completed"}
        mock_get_pod_ip.return_value = "10.0.0.5"
        from api.proxy import _resolve_pod_url
        result = _resolve_pod_url("TASK-1")
        self.assertIsNotNone(result)
        self.assertIn("10.0.0.5", result)

    @patch("k8s_client.get_pod_phase")
    @patch("k8s_client.get_pod_ip")
    @patch("database.get_ticket")
    def test_failed_ticket_still_resolves(self, mock_get_ticket, mock_get_pod_ip, mock_get_pod_phase):
        mock_get_ticket.return_value = {"id": "TASK-1", "status": "failed"}
        mock_get_pod_ip.return_value = "10.0.0.5"
        from api.proxy import _resolve_pod_url
        result = _resolve_pod_url("TASK-1")
        self.assertIsNotNone(result)
        self.assertIn("10.0.0.5", result)

    @patch("k8s_client.get_pod_phase")
    @patch("k8s_client.get_pod_ip")
    @patch("database.get_ticket")
    def test_merged_ticket_still_resolves(self, mock_get_ticket, mock_get_pod_ip, mock_get_pod_phase):
        mock_get_ticket.return_value = {"id": "TASK-1", "status": "merged"}
        mock_get_pod_ip.return_value = "10.0.0.5"
        from api.proxy import _resolve_pod_url
        result = _resolve_pod_url("TASK-1")
        self.assertIsNotNone(result)
        self.assertIn("10.0.0.5", result)

    @patch("database.get_ticket")
    def test_missing_ticket_returns_none(self, mock_get_ticket):
        mock_get_ticket.return_value = None
        from api.proxy import _resolve_pod_url
        result = _resolve_pod_url("TASK-MISSING")
        self.assertIsNone(result)

    @patch("k8s_client.get_pod_phase")
    @patch("k8s_client.get_pod_ip")
    @patch("database.get_ticket")
    def test_pod_name_is_lowercased(self, mock_get_ticket, mock_get_pod_ip, mock_get_pod_phase):
        mock_get_ticket.return_value = {"id": "TASK-XYZ", "status": "running"}
        mock_get_pod_ip.return_value = None
        mock_get_pod_phase.return_value = "Running"
        from api.proxy import _resolve_pod_url
        result = _resolve_pod_url("TASK-XYZ")
        self.assertIsNotNone(result)
        self.assertIn("agent-worker-task-xyz", result)


if __name__ == "__main__":
    unittest.main()