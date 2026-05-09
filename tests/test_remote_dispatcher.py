import json
import unittest
from unittest.mock import MagicMock, patch

from orchid.remote.dispatcher import RemoteDispatcher, RemoteDispatcherError
from orchid.remote.types import WorkerNode
from orchid.worker_protocol import TaskContext, WorkerResult


def _make_ctx() -> TaskContext:
    return TaskContext(
        task_id="T001",
        task_description="Build a REST API endpoint",
        session_context="session-abc",
        agent_type="developer",
        model_key="local",
        project_dir="/home/user/project",
        injection_queue_path="/tmp/injection_queue",
    )


class TestRemoteDispatcher(unittest.TestCase):
    """Tests for RemoteDispatcher dispatch() behavior."""

    def test_dispatch_posts_to_node_url(self):
        """dispatch() calls httpx.post with a URL containing '/task'."""
        node1 = WorkerNode(node_id="n1", url="http://10.0.0.1:8080")
        node2 = WorkerNode(node_id="n2", url="http://10.0.0.2:8080")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "worker_result_json": WorkerResult(
                task_id="T001", success=True, result="ok", duration_s=1.0
            ).to_json(),
            "node_id": "n1",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("orchid.remote.dispatcher.httpx.post", return_value=mock_response) as mock_post:
            dispatcher = RemoteDispatcher([node1, node2])
            ctx = _make_ctx()
            dispatcher.dispatch(ctx)

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            url = call_args.args[0]
            self.assertIn("/task", url)

    def test_dispatch_decrements_load_on_success(self):
        """After a successful dispatch, node.current_load returns to its original value."""
        node1 = WorkerNode(node_id="n1", url="http://10.0.0.1:8080", capacity=4, current_load=0)
        node2 = WorkerNode(node_id="n2", url="http://10.0.0.2:8080", capacity=4, current_load=0)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "worker_result_json": WorkerResult(
                task_id="T001", success=True, result="ok", duration_s=1.0
            ).to_json(),
            "node_id": "n1",
        }
        mock_response.raise_for_status = MagicMock()

        with patch("orchid.remote.dispatcher.httpx.post", return_value=mock_response):
            dispatcher = RemoteDispatcher([node1, node2])
            ctx = _make_ctx()
            dispatcher.dispatch(ctx)

            # Load should be back to 0 after the finally block decrements it
            self.assertEqual(node1.current_load, 0)
            self.assertEqual(node2.current_load, 0)

    def test_dispatch_raises_when_no_nodes_available(self):
        """When all nodes have capacity=0, dispatch raises RemoteDispatcherError."""
        node = WorkerNode(node_id="n1", url="http://10.0.0.1:8080", capacity=0, current_load=0)

        dispatcher = RemoteDispatcher([node])
        ctx = _make_ctx()

        with self.assertRaises(RemoteDispatcherError) as cm:
            dispatcher.dispatch(ctx)

        self.assertIn("No available worker nodes", str(cm.exception))


if __name__ == "__main__":
    unittest.main()