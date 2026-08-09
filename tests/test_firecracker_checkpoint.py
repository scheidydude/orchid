from pathlib import Path

from orchid.firecracker_checkpoint import FirecrackerCheckpoint, FirecrackerCheckpointStore


def _sample_checkpoint(task_id: str = "T001", work_dir: str = "/tmp/orchid-fc-T001") -> FirecrackerCheckpoint:
    return FirecrackerCheckpoint(
        task_id=task_id,
        work_dir=work_dir,
        snapshot_path=f"{work_dir}/snapshot_file",
        mem_file_path=f"{work_dir}/mem_file",
        rootfs_path=f"{work_dir}/rootfs.ext4",
        vsock_uds_path=f"{work_dir}/vsock.sock",
    )


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = FirecrackerCheckpointStore(root=tmp_path)
    cp = _sample_checkpoint()
    store.save(cp)

    loaded = store.load_for_task("T001")
    assert loaded is not None
    assert loaded.task_id == "T001"
    assert loaded.snapshot_path == cp.snapshot_path
    assert loaded.mem_file_path == cp.mem_file_path
    assert loaded.rootfs_path == cp.rootfs_path
    assert loaded.vsock_uds_path == cp.vsock_uds_path
    assert loaded.created_at == cp.created_at


def test_load_for_task_returns_none_when_missing(tmp_path: Path) -> None:
    store = FirecrackerCheckpointStore(root=tmp_path)
    assert store.load_for_task("does-not-exist") is None


def test_has_checkpoint(tmp_path: Path) -> None:
    store = FirecrackerCheckpointStore(root=tmp_path)
    assert store.has_checkpoint("T001") is False
    store.save(_sample_checkpoint())
    assert store.has_checkpoint("T001") is True


def test_delete_removes_checkpoint(tmp_path: Path) -> None:
    store = FirecrackerCheckpointStore(root=tmp_path)
    store.save(_sample_checkpoint())
    assert store.has_checkpoint("T001") is True
    store.delete("T001")
    assert store.has_checkpoint("T001") is False


def test_delete_nonexistent_is_a_noop(tmp_path: Path) -> None:
    store = FirecrackerCheckpointStore(root=tmp_path)
    store.delete("never-existed")  # must not raise


def test_load_for_task_handles_corrupt_file(tmp_path: Path) -> None:
    store = FirecrackerCheckpointStore(root=tmp_path)
    (tmp_path / "T001.json").write_text("not valid json{{{")
    assert store.load_for_task("T001") is None


def test_checkpoints_for_different_tasks_are_independent(tmp_path: Path) -> None:
    store = FirecrackerCheckpointStore(root=tmp_path)
    store.save(_sample_checkpoint(task_id="T001", work_dir="/tmp/a"))
    store.save(_sample_checkpoint(task_id="T002", work_dir="/tmp/b"))

    assert store.load_for_task("T001").work_dir == "/tmp/a"
    assert store.load_for_task("T002").work_dir == "/tmp/b"

    store.delete("T001")
    assert store.load_for_task("T001") is None
    assert store.load_for_task("T002") is not None
