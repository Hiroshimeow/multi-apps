import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .base import BaseSessionManager
from ..config import slugify_app_id
from ..runtime.models import RUN_STATES, RunRecord
from ..runtime.process_client import ProcessClient
from ..runtime.process_identity import process_matches
from ..runtime.registry import RuntimeRegistry

ACTIVE_STATES = frozenset({"starting", "running", "stopping"})


class SubprocessSessionManager(BaseSessionManager):
    def __init__(self, global_config):
        super().__init__(global_config)
        config_dir = Path(self.config.get("_config_dir") or os.getcwd()).resolve()
        self.runtime_dir = config_dir / ".runtime"
        self.registry = RuntimeRegistry(self.runtime_dir)
        self.project_root = Path(__file__).resolve().parents[2]
        self.client = ProcessClient(self.runtime_dir, self.project_root)
        self.reconcile()

    def start(self, runner):
        app_id = str(runner.app_config.get("id") or slugify_app_id(runner.name))
        self.reconcile(app_id)
        app_records = self._records_for(app_id)
        active = [record for record in app_records if record.state in ACTIVE_STATES]
        orphaned = [record for record in app_records if record.state == "orphaned"]
        if (active or orphaned) and not runner.app_config.get("multi_run", False):
            if orphaned and not active:
                return False, (
                    f"App '{runner.name}' has an orphaned run whose identity is uncertain."
                )
            return False, f"App '{runner.name}' is already running."

        workdir = runner.get_workdir()
        if workdir and not os.path.isdir(workdir):
            return False, f"Path is not a directory: {workdir}"

        run_id = uuid4().hex
        log_dir = self._log_dir() / app_id
        stdout_path = log_dir / f"{run_id}.out.log"
        stderr_path = log_dir / f"{run_id}.err.log"
        record = RunRecord.create(
            app_id=app_id,
            run_id=run_id,
            path=workdir or "",
            command=runner.app_config["command"],
            args=list(runner.app_config.get("args") or []),
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            close_timeout=float(runner.app_config.get("close_timeout", 5.0)),
        )
        self.registry.save(record)

        try:
            keeper_pid, keeper_created_at = self.client.launch_keeper(run_id)
            self.registry.update(
                run_id,
                keeper_pid=keeper_pid,
                keeper_created_at=keeper_created_at,
            )
            response = self.client.wait_until_ready(run_id)
        except Exception as exc:
            current = self.registry.load(run_id)
            if current is not None and current.state not in {"stopped", "failed"}:
                self.registry.update(run_id, state="failed")
            return False, f"Keeper failed to start: {exc}"

        root_pid = int(response.get("root_pid") or 0)
        return True, f"Started run {run_id} with PID {root_pid}"

    def stop(self, app_name):
        self.reconcile(app_name)
        records = self._records_for(
            app_name, states=set(ACTIVE_STATES).union({"orphaned"})
        )
        if not records:
            return False, "App not found in session manager."

        failures = []
        requested = []
        for record in records:
            if record.state == "orphaned":
                failures.append(
                    f"Run {record.run_id} is orphaned; refusing to stop an unverified process"
                )
                continue
            if not process_matches(record.keeper_pid, record.keeper_created_at):
                self._update_if_changed(record, state="orphaned")
                failures.append(
                    f"Run {record.run_id} keeper identity could not be verified"
                )
                continue
            try:
                response = self.client.stop(record.run_id)
            except (ConnectionError, TimeoutError) as exc:
                self._update_if_changed(record, state="orphaned")
                failures.append(f"Run {record.run_id}: {exc}")
                continue
            if not response.get("ok"):
                failures.append(
                    f"Run {record.run_id}: {response.get('message', 'stop rejected')}"
                )
                continue
            requested.append(record)

        for record in requested:
            deadline = time.monotonic() + record.close_timeout + 5.0
            while time.monotonic() < deadline:
                current = self.registry.load(record.run_id)
                keeper_alive = process_matches(
                    record.keeper_pid, record.keeper_created_at
                )
                root_alive = process_matches(record.root_pid, record.root_created_at)
                if not keeper_alive and not root_alive:
                    if current is not None and current.state != "stopped":
                        self.registry.update(record.run_id, state="stopped")
                    break
                time.sleep(0.05)
            else:
                alive = []
                if process_matches(record.keeper_pid, record.keeper_created_at):
                    alive.append("keeper")
                if process_matches(record.root_pid, record.root_created_at):
                    alive.append("root")
                failures.append(
                    f"Run {record.run_id} did not stop before timeout"
                    + (f" ({', '.join(alive)} still alive)" if alive else "")
                )
            self.client.reap_finished(record.run_id)

        if failures:
            return False, "; ".join(failures)
        return True, "Stopped."

    def is_running(self, app_name):
        return bool(self._active_records(app_name))

    def get_info(self, app_name):
        app_records = self._records_for(app_name)
        records = [record for record in app_records if record.state in ACTIVE_STATES]
        if not records:
            orphaned = [record for record in app_records if record.state == "orphaned"]
            if orphaned:
                newest = max(orphaned, key=lambda item: item.created_at)
                return {
                    "status": "ORPHANED",
                    "instances": len(orphaned),
                    "run_id": newest.run_id,
                    "keeper_pid": newest.keeper_pid,
                    "pid": newest.root_pid,
                    "stdout_path": newest.stdout_path,
                    "stderr_path": newest.stderr_path,
                }
            return {"status": "STOPPED"}

        newest = max(records, key=lambda item: item.created_at)
        created_at = self._parse_timestamp(newest.created_at)
        uptime = datetime.now(timezone.utc) - created_at
        total_seconds = max(0, int(uptime.total_seconds()))
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        status = {
            "starting": "STARTING",
            "running": "RUNNING",
            "stopping": "STOPPING",
        }.get(newest.state, newest.state.upper())
        return {
            "status": status,
            "pid": newest.root_pid,
            "root_created_at": newest.root_created_at,
            "keeper_pid": newest.keeper_pid,
            "keeper_created_at": newest.keeper_created_at,
            "run_id": newest.run_id,
            "instances": len(records),
            "uptime": f"{days}d {hours}h {minutes}m {seconds}s",
            "start_time": created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": newest.created_at,
            "stdout_path": newest.stdout_path,
            "stderr_path": newest.stderr_path,
        }

    def list_runs(self, app_name=None):
        self.reconcile(app_name)
        if app_name is None:
            records = self.registry.list_records()
        else:
            records = self._records_for(app_name)
        return [record.to_dict() for record in records]

    def reconcile(self, app_name=None):
        records = (
            self.registry.list_records()
            if app_name is None
            else self._records_for(app_name)
        )
        if app_name is not None or len(records) < 2:
            return [self._reconcile_record(record) for record in records]

        worker_count = min(8, len(records))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            return list(executor.map(self._reconcile_record, records))

    def _reconcile_record(self, record):
        if record.state in {"stopped", "failed"}:
            return record

        keeper_alive = process_matches(record.keeper_pid, record.keeper_created_at)
        root_alive = process_matches(record.root_pid, record.root_created_at)
        if not keeper_alive:
            target_state = "orphaned" if root_alive else "stopped"
            return self._update_if_changed(record, state=target_state)

        try:
            response = self.client.status(record.run_id, timeout=0.5)
        except (ConnectionError, TimeoutError):
            return self._update_if_changed(record, state="orphaned")

        if not response.get("ok") or not self._status_identity_matches(record, response):
            return self._update_if_changed(record, state="orphaned")

        state = str(response.get("state") or "")
        if state not in RUN_STATES:
            return self._update_if_changed(record, state="orphaned")

        root_pid = int(response.get("root_pid") or 0)
        root_created_at = float(response.get("root_created_at") or 0.0)
        reported_root_alive = process_matches(root_pid, root_created_at)
        tree_alive = bool(response.get("tree_alive", reported_root_alive))
        if state == "running" and (not reported_root_alive or not tree_alive):
            return self._update_if_changed(record, state="orphaned")
        if state in {"stopped", "failed"} and tree_alive:
            return self._update_if_changed(record, state="orphaned")

        changes = {
            "state": state,
            "root_pid": root_pid,
            "root_created_at": root_created_at,
            "exit_code": response.get("exit_code"),
        }
        return self._update_if_changed(record, **changes)

    @staticmethod
    def _status_identity_matches(record, response):
        if response.get("run_id") != record.run_id:
            return False
        if response.get("app_id") != record.app_id:
            return False
        if int(response.get("keeper_pid") or 0) != record.keeper_pid:
            return False
        reported_created_at = float(response.get("keeper_created_at") or 0.0)
        return abs(reported_created_at - record.keeper_created_at) <= 0.25

    def _update_if_changed(self, record, **changes):
        effective = {
            key: value for key, value in changes.items() if getattr(record, key) != value
        }
        if not effective:
            return record
        updated = self.registry.update(
            record.run_id,
            expected_updated_at=record.updated_at,
            **effective,
        )
        return updated if updated is not None else record

    def _active_records(self, app_name):
        self.client.reap_finished()
        return self._records_for(app_name, states=ACTIVE_STATES)

    def _records_for(self, app_name, states=None):
        candidates = {str(app_name), slugify_app_id(str(app_name))}
        return [
            record
            for record in self.registry.list_records()
            if record.app_id in candidates and (states is None or record.state in states)
        ]

    def _log_dir(self):
        log_dir = Path(self.config.get("log_dir", "./logs"))
        if not log_dir.is_absolute():
            config_dir = Path(self.config.get("_config_dir") or os.getcwd())
            log_dir = config_dir / log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir.resolve()

    @staticmethod
    def _parse_timestamp(value):
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)
