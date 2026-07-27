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
STARTING_IPC_GRACE_SECONDS = 10.0
STARTING_CLOCK_SKEW_TOLERANCE_SECONDS = 2.0
STARTING_STOP_RETRY_TIMEOUT = 3.0
STOP_IPC_RETRY_INTERVAL = 0.05


class SubprocessSessionManager(BaseSessionManager):
    def __init__(self, global_config):
        super().__init__(global_config)
        config_dir = Path(self.config.get("_config_dir") or os.getcwd()).resolve()
        self.runtime_dir = config_dir / ".runtime"
        self.registry = RuntimeRegistry(self.runtime_dir)
        self.project_root = Path(__file__).resolve().parents[2]
        self.client = ProcessClient(self.runtime_dir, self.project_root)
        self.reconcile()

    def start(self, runner, *, wait_for_ready=False):
        app_id = str(runner.app_config.get("id") or slugify_app_id(runner.name))
        workdir = runner.get_workdir()
        if workdir and not os.path.isdir(workdir):
            return False, f"Path is not a directory: {workdir}"

        if runner.app_config.get("multi_run", False):
            record = self._create_start_record(runner, app_id, workdir)
            self.registry.save(record)
        else:
            with self.registry.app_lock(app_id):
                app_records = self._refresh_dead_active_records(app_id)
                active = [record for record in app_records if record.state in ACTIVE_STATES]
                orphaned = [record for record in app_records if record.state == "orphaned"]
                if active or orphaned:
                    if orphaned and not active:
                        return False, (
                            f"App '{runner.name}' has an orphaned run whose identity is uncertain."
                        )
                    return False, f"App '{runner.name}' is already running."
                record = self._create_start_record(runner, app_id, workdir)
                self.registry.save(record)

        run_id = record.run_id
        try:
            keeper_pid, keeper_created_at = self.client.launch_keeper(run_id)
            record.keeper_pid = keeper_pid
            record.keeper_created_at = keeper_created_at
            current = self.registry.update(
                run_id,
                keeper_pid=keeper_pid,
                keeper_created_at=keeper_created_at,
            )
            if current is None:
                raise RuntimeError(f"Run record disappeared: {run_id}")
        except Exception as exc:
            return self._recover_or_cleanup_start(record, exc)

        if current.state in {"failed", "stopped"}:
            return False, f"Keeper entered terminal state: {current.state}"
        if wait_for_ready:
            return self._wait_for_start_ready(current)
        return True, f"Accepted run {run_id}; starting with keeper PID {keeper_pid}"

    def _create_start_record(self, runner, app_id, workdir):
        run_id = uuid4().hex
        log_dir = self._log_dir() / app_id
        return RunRecord.create(
            app_id=app_id,
            run_id=run_id,
            path=workdir or "",
            command=runner.app_config["command"],
            args=list(runner.app_config.get("args") or []),
            stdout_path=str(log_dir / f"{run_id}.out.log"),
            stderr_path=str(log_dir / f"{run_id}.err.log"),
            close_timeout=float(runner.app_config.get("close_timeout", 5.0)),
        )

    def _wait_for_start_ready(self, record):
        try:
            response = self.client.wait_until_ready(record.run_id)
        except Exception as exc:
            return self._recover_or_cleanup_start(record, exc)

        root_pid = int(response.get("root_pid") or 0)
        return True, f"Started run {record.run_id} with PID {root_pid}"

    def _recover_or_cleanup_start(self, record, error):
        current = self.registry.load(record.run_id) or record
        if record.keeper_pid > 0 and record.keeper_created_at > 0:
            current.keeper_pid = record.keeper_pid
            current.keeper_created_at = record.keeper_created_at
        keeper_alive, root_alive = self._record_processes_alive(current)

        if keeper_alive:
            response = None
            try:
                response = self.client.status(current.run_id, timeout=0.5)
            except (ConnectionError, TimeoutError):
                pass

            if (
                response is not None
                and response.get("ok")
                and self._status_identity_matches(current, response)
            ):
                state = str(response.get("state") or "")
                root_pid = int(response.get("root_pid") or 0)
                root_created_at = float(response.get("root_created_at") or 0.0)
                tree_alive = bool(response.get("tree_alive", False))
                if root_pid > 0 and root_created_at > 0:
                    current.root_pid = root_pid
                    current.root_created_at = root_created_at
                    self.registry.update(
                        current.run_id,
                        keeper_pid=current.keeper_pid,
                        keeper_created_at=current.keeper_created_at,
                        root_pid=root_pid,
                        root_created_at=root_created_at,
                    )
                if (
                    state == "running"
                    and tree_alive
                    and process_matches(root_pid, root_created_at)
                ):
                    self.registry.update(
                        current.run_id,
                        state="running",
                        keeper_pid=current.keeper_pid,
                        keeper_created_at=current.keeper_created_at,
                        root_pid=root_pid,
                        root_created_at=root_created_at,
                        exit_code=response.get("exit_code"),
                    )
                    return True, f"Started run {current.run_id} with PID {root_pid}"
                if state in {"stopped", "failed"} and not tree_alive:
                    if self._wait_for_verified_exit(current, timeout=2.0):
                        return False, f"Keeper failed to start: {error}"

            stop_response = None
            try:
                stop_response = self.client.stop(current.run_id, timeout=1.0)
            except (ConnectionError, TimeoutError):
                pass
            if stop_response and stop_response.get("ok"):
                timeout = current.close_timeout + 5.0
                if self._wait_for_verified_exit(current, timeout=timeout):
                    self.registry.update(
                        current.run_id,
                        state="failed",
                        keeper_pid=current.keeper_pid,
                        keeper_created_at=current.keeper_created_at,
                    )
                    return False, f"Keeper failed to start: {error}"

            latest = self.registry.load(current.run_id) or current
            keeper_alive, root_alive = self._record_processes_alive(latest)
            if not keeper_alive and not root_alive:
                self.registry.update(
                    latest.run_id,
                    state="failed",
                    keeper_pid=current.keeper_pid,
                    keeper_created_at=current.keeper_created_at,
                )
                return False, f"Keeper failed to start: {error}"
            self.registry.update(
                latest.run_id,
                state="orphaned",
                keeper_pid=current.keeper_pid,
                keeper_created_at=current.keeper_created_at,
            )
            return False, (
                f"Keeper readiness failed and cleanup could not be verified: {error}"
            )

        target_state = "orphaned" if root_alive else "failed"
        self.registry.update(
            current.run_id,
            state=target_state,
            keeper_pid=current.keeper_pid,
            keeper_created_at=current.keeper_created_at,
        )
        if target_state == "orphaned":
            return False, (
                f"Keeper failed and a managed process may still be alive: {error}"
            )
        return False, f"Keeper failed to start: {error}"

    def _wait_for_verified_exit(self, record, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.registry.load(record.run_id) or record
            keeper_alive, root_alive = self._record_processes_alive(current)
            if not keeper_alive and not root_alive:
                return True
            time.sleep(0.05)
        current = self.registry.load(record.run_id) or record
        return not any(self._record_processes_alive(current))

    @staticmethod
    def _record_processes_alive(record):
        return (
            process_matches(record.keeper_pid, record.keeper_created_at),
            process_matches(record.root_pid, record.root_created_at),
        )

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
            requested_record, error = self._request_stop_with_retry(record)
            if requested_record is None:
                failures.append(f"Run {record.run_id}: {error}")
                continue
            requested.append(requested_record)

        for record in requested:
            deadline = time.monotonic() + record.close_timeout + 5.0
            while time.monotonic() < deadline:
                current = self.registry.load(record.run_id) or record
                keeper_alive = process_matches(
                    current.keeper_pid, current.keeper_created_at
                )
                root_alive = process_matches(
                    current.root_pid, current.root_created_at
                )
                if not keeper_alive and not root_alive:
                    if current.state != "stopped":
                        self.registry.update(record.run_id, state="stopped")
                    break
                time.sleep(0.05)
            else:
                current = self.registry.load(record.run_id) or record
                alive = []
                if process_matches(current.keeper_pid, current.keeper_created_at):
                    alive.append("keeper")
                if process_matches(current.root_pid, current.root_created_at):
                    alive.append("root")
                failures.append(
                    f"Run {record.run_id} did not stop before timeout"
                    + (f" ({', '.join(alive)} still alive)" if alive else "")
                )
            self.client.wait_for_reap(record.run_id, timeout=2.0)

        if failures:
            return False, "; ".join(failures)
        return True, "Stopped."

    def _request_stop_with_retry(self, record):
        deadline = time.monotonic() + (
            STARTING_STOP_RETRY_TIMEOUT if record.state == "starting" else 1.0
        )
        last_error = None

        while True:
            current = self.registry.load(record.run_id) or record
            keeper_alive, root_alive = self._record_processes_alive(current)
            if not keeper_alive:
                target_state = "orphaned" if root_alive else "stopped"
                self._update_if_changed(current, state=target_state)
                if root_alive:
                    return None, "keeper identity was lost while the root remained alive"
                return None, "keeper exited before Stop could be delivered"

            try:
                response = self.client.stop(current.run_id, timeout=0.5)
            except (ConnectionError, TimeoutError) as exc:
                last_error = exc
                if time.monotonic() < deadline:
                    time.sleep(STOP_IPC_RETRY_INTERVAL)
                    continue
                self._transition_after_uncontrollable_stop(current)
                return None, (
                    "verified keeper did not expose Stop IPC before the retry timeout: "
                    f"{last_error}"
                )

            if response.get("ok"):
                latest = self.registry.load(current.run_id) or current
                return latest, None
            return None, response.get("message", "stop rejected")

    def _transition_after_uncontrollable_stop(self, record):
        latest = record
        for _ in range(3):
            latest = self.registry.load(record.run_id) or latest
            keeper_alive, root_alive = self._record_processes_alive(latest)
            target_state = "orphaned" if keeper_alive or root_alive else "stopped"
            if latest.state in {"stopped", "failed"}:
                return latest
            updated = self.registry.update(
                latest.run_id,
                expected_updated_at=latest.updated_at,
                state=target_state,
            )
            if updated is None or updated.state == target_state:
                return updated
        return latest

    def is_running(self, app_name):
        return bool(self._active_records(app_name))

    def get_status_snapshot(self, app_ids):
        requested = tuple(dict.fromkeys(str(value) for value in app_ids))
        snapshot = {
            app_id: {"status": "STOPPED", "instances": 0}
            for app_id in requested
        }
        if not requested:
            return snapshot

        self.client.reap_finished()
        grouped = {app_id: [] for app_id in requested}
        candidate_to_requested = {}
        for app_id in requested:
            for candidate in {app_id, slugify_app_id(app_id)}:
                candidate_to_requested.setdefault(candidate, []).append(app_id)
        records = self.registry.indexed_records(
            candidate_to_requested,
            include_latest=True,
        )
        for record in records:
            for app_id in candidate_to_requested.get(record.app_id, ()):
                grouped[app_id].append(record)

        for app_id, app_records in grouped.items():
            snapshot[app_id] = self._status_from_records(
                self._refresh_dead_records(app_records)
            )
        return snapshot

    def get_info(self, app_name):
        app_name = str(app_name)
        return self.get_status_snapshot((app_name,))[app_name]

    def _status_from_records(self, app_records):
        records = [record for record in app_records if record.state in ACTIVE_STATES]
        if records:
            newest = max(records, key=lambda item: (item.created_at, item.run_id))
            status = {
                "starting": "STARTING",
                "running": "RUNNING",
                "stopping": "STOPPING",
            }.get(newest.state, newest.state.upper())
            return self._run_info_payload(newest, status, len(records))

        orphaned = [record for record in app_records if record.state == "orphaned"]
        if orphaned:
            newest = max(orphaned, key=lambda item: (item.created_at, item.run_id))
            return self._run_info_payload(newest, "ORPHANED", len(orphaned))

        if app_records:
            newest = max(app_records, key=lambda item: (item.created_at, item.run_id))
            return self._run_info_payload(newest, "STOPPED", 0)
        return {"status": "STOPPED", "instances": 0}

    def _run_info_payload(self, record, status, instances):
        created_at = self._parse_timestamp(record.created_at)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        updated_at = self._parse_timestamp(record.updated_at)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        uptime = datetime.now(timezone.utc) - created_at
        total_seconds = max(0, int(uptime.total_seconds()))
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        return {
            "status": status,
            "state": record.state,
            "pid": record.root_pid,
            "root_created_at": record.root_created_at,
            "keeper_pid": record.keeper_pid,
            "keeper_created_at": record.keeper_created_at,
            "run_id": record.run_id,
            "instances": instances,
            "uptime": f"{days}d {hours}h {minutes}m {seconds}s",
            "start_time": created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "last_used_time": updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "stdout_path": record.stdout_path,
            "stderr_path": record.stderr_path,
            "exit_code": record.exit_code,
        }

    def list_runs(self, app_name=None):
        self.reconcile(app_name)
        records = self.registry.list_records()
        if app_name is not None:
            candidates = {str(app_name), slugify_app_id(str(app_name))}
            records = [record for record in records if record.app_id in candidates]
        return [record.to_dict() for record in records]

    def reconcile(self, app_name=None):
        records = (
            self.registry.indexed_records(include_latest=False)
            if app_name is None
            else self._records_for(
                app_name,
                states=set(ACTIVE_STATES).union({"orphaned"}),
            )
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
            if record.state == "starting" and self._starting_within_ipc_grace(record):
                return record
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

    def _refresh_dead_active_records(self, app_name):
        return self._refresh_dead_records(self._records_for(app_name))

    def _refresh_dead_records(self, records):
        refreshed = []
        for record in records:
            if record.state == "orphaned":
                keeper_alive, root_alive = self._record_processes_alive(record)
                if keeper_alive or root_alive:
                    refreshed.append(record)
                else:
                    refreshed.append(self._update_if_changed(record, state="stopped"))
                continue
            if record.state not in ACTIVE_STATES:
                refreshed.append(record)
                continue
            keeper_alive, root_alive = self._record_processes_alive(record)
            if record.state == "starting":
                if self._starting_within_ipc_grace(record):
                    refreshed.append(record)
                    continue
                if keeper_alive:
                    refreshed.append(self._update_if_changed(record, state="orphaned"))
                    continue
            elif keeper_alive:
                refreshed.append(record)
                continue
            target_state = "orphaned" if root_alive else "stopped"
            refreshed.append(self._update_if_changed(record, state=target_state))
        return refreshed

    def _active_records(self, app_name):
        self.client.reap_finished()
        return [
            record
            for record in self._refresh_dead_active_records(app_name)
            if record.state in ACTIVE_STATES
        ]

    def _records_for(self, app_name, states=None):
        candidates = {str(app_name), slugify_app_id(str(app_name))}
        return [
            record
            for record in self.registry.indexed_records(candidates, include_latest=True)
            if record.app_id in candidates and (states is None or record.state in states)
        ]

    def _log_dir(self):
        log_dir = Path(self.config.get("log_dir", "./logs"))
        if not log_dir.is_absolute():
            config_dir = Path(self.config.get("_config_dir") or os.getcwd())
            log_dir = config_dir / log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir.resolve()

    def _starting_within_ipc_grace(self, record):
        return self._record_age_seconds(record) <= STARTING_IPC_GRACE_SECONDS

    @staticmethod
    def _record_age_seconds(record):
        try:
            created_at = datetime.fromisoformat(record.created_at)
        except (TypeError, ValueError):
            return float("inf")
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - created_at).total_seconds()
        if age_seconds < -STARTING_CLOCK_SKEW_TOLERANCE_SECONDS:
            return float("inf")
        return max(0.0, age_seconds)

    @staticmethod
    def _parse_timestamp(value):
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)
