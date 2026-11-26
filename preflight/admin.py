import json
import re
import subprocess


class FlagsmithError(Exception):
    def __init__(self, message: str, status: int = 0, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


class Admin:
    """Talks to Flagsmith through the logged-in `flagsmith` CLI, so Preflight never holds an admin key."""

    def __init__(self, environment: str, project: str):
        self.environment = environment
        self.project = str(project)

    def run(self, args: list[str], body=None):
        cmd = ["flagsmith", *args, "-e", self.environment, "-p", self.project, "--no-input"]
        proc = subprocess.run(
            cmd,
            input=json.dumps(body) if body is not None else None,
            capture_output=True,
            text=True,
        )
        out = proc.stdout.strip()
        try:
            data = json.loads(out) if out else None
        except json.JSONDecodeError:
            data = out
        if proc.returncode != 0:
            err = next((l for l in proc.stderr.splitlines() if l.startswith("Error")), proc.stderr.strip())
            m = re.search(r"returned (\d{3})", err)
            raise FlagsmithError(f"{err} {out}".strip(), int(m.group(1)) if m else 0, data)
        return data

    def api(self, path: str, method: str = "GET", body=None):
        args = ["api", path, "-X", method]
        if body is not None:
            args += ["--input", "-"]
        return self.run(args, body)

    def env(self, suffix: str) -> str:
        return f"api/v1/environments/{self.environment}/{suffix}"

    def warehouses(self) -> list[dict]:
        return self.api(self.env("warehouse-connections/")) or []

    def feature(self, name: str) -> dict | None:
        try:
            return self.run(["feature", "get", name, "--json"])
        except FlagsmithError as e:
            if e.status == 404 or "not found" in str(e).lower():
                return None
            raise

    def metric(self, metric_id: int) -> dict:
        return self.api(self.env(f"experiment-metrics/{metric_id}/"))

    def metrics(self) -> list[dict]:
        return self.api(self.env("experiment-metrics/"))["results"]

    def experiments(self) -> list[dict]:
        return self.api(self.env("experiments/"))["results"]

    def experiment(self, experiment_id: int) -> dict:
        return self.api(self.env(f"experiments/{experiment_id}/"))

    def experiment_for(self, flag: str) -> dict | None:
        mine = [e for e in self.experiments() if e["feature"]["name"] == flag]
        active = [e for e in mine if e["status"] != "completed"]
        chosen = (active or mine or [None])[0]
        return self.experiment(chosen["id"]) if chosen else None

    def start(self, experiment_id: int) -> dict:
        return self.api(self.env(f"experiments/{experiment_id}/start/"), "POST", {})

    def complete(self, experiment_id: int, variant_id: int | None) -> dict:
        body = {"rollout_decision": "variant", "rollout_variant": variant_id} if variant_id else {"rollout_decision": "control"}
        return self.api(self.env(f"experiments/{experiment_id}/complete/"), "POST", body)

    def refresh(self, experiment_id: int, what: str) -> int | None:
        # Returns seconds to wait when Flagsmith throttles the refresh (once every 5 minutes).
        try:
            self.api(self.env(f"experiments/{experiment_id}/{what}/refresh/"), "POST", {})
            return None
        except FlagsmithError as e:
            if e.status != 429:
                raise
            m = re.search(r"(\d+) seconds", json.dumps(e.body))
            return int(m.group(1)) if m else 300

    def results(self, experiment_id: int) -> dict | None:
        return self.api(self.env(f"experiments/{experiment_id}/results/"))["results"]

    def exposures(self, experiment_id: int) -> dict | None:
        return self.api(self.env(f"experiments/{experiment_id}/exposures/"))["exposures"]

    def create_metric(self, name: str, event: str) -> dict:
        body = {"name": name, "aggregation": "occurrence", "direction": "up", "definition": {"version": 1, "event": event}}
        return self.api(self.env("experiment-metrics/"), "POST", body)

    def create_experiment(self, feature: dict, name: str, hypothesis: str, metric_id: int, split: dict[int, float]) -> dict:
        body = {
            "feature": feature["id"],
            "name": name,
            "hypothesis": hypothesis,
            "metrics": [{"metric": metric_id, "expected_direction": "increase"}],
            "experiment_rollout": {
                "enabled": True,
                "rollout_percentage": 100,
                "feature_state_value": {"type": "string", "value": feature["default_value"]},
                "multivariate_feature_state_values": [
                    {"multivariate_feature_option": vid, "percentage_allocation": pct} for vid, pct in split.items()
                ],
            },
        }
        return self.api(self.env("experiments/"), "POST", body)

    def create_flag(self, name: str, control: str, variants: dict[str, str]) -> dict:
        self.run(["feature", "create", name, "--value", control, "--enabled"])
        for key, value in variants.items():
            self.run(["feature", "variant", "add", name, "--value", value, "--key", key, "--weight", "0"])
        return self.feature(name)

    def set_variant_key(self, flag: str, variant_id: int, key: str) -> None:
        self.run(["feature", "variant", "update", flag, str(variant_id), "--key", key])

    def archive(self, flag: str) -> None:
        self.run(["feature", "update", flag, "--archive"])

    def evaluate(self, flag: str, identity: str) -> dict:
        return self.run(["evaluate", flag, "--identity", identity, "--json"])
