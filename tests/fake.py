import copy

FEATURE = {
    "id": 1,
    "name": "signup_cta",
    "type": "multivariate",
    "default_value": "Join the waitlist",
    "enabled": True,
    "variants": [{"id": 11, "value": "Get early access", "weight": 0, "key": "early_access"}],
}

EXPERIMENT = {
    "id": 7,
    "name": "Signup CTA",
    "status": "created",
    "feature": {
        "id": 1,
        "name": "signup_cta",
        "multivariate_options": [{"id": 11, "string_value": "Get early access", "key": "early_access"}],
    },
    "metrics": [{"metric": 5, "metric_name": "Signups"}],
    "experiment_rollout": {"rollout_percentage": 100.0},
    "started_at": None,
}


def results(control=(150, 15), treatment=(150, 21), inference=None, srm=None):
    return {
        "as_of": "2026-11-27T10:00:00+00:00",
        "refresh_requested_at": "2026-11-27T09:59:00+00:00",
        "last_error_at": None,
        "is_final": False,
        "payload": {
            "srm_p_value": srm,
            "metrics": [{
                "metric_id": 5,
                "variants": {
                    "control": {"n": control[0], "sum": control[1], "sum_squares": control[1]},
                    "early_access": {"n": treatment[0], "sum": treatment[1], "sum_squares": treatment[1]},
                },
                "inference": {"early_access": inference},
            }],
        },
    }


class FakeAdmin:
    project, environment = "55614", "env-key"

    def __init__(self, warehouse="connected", feature=FEATURE, experiment=EXPERIMENT, event="signup", results=None):
        self.warehouse = warehouse
        self._feature = copy.deepcopy(feature)
        self._experiment = copy.deepcopy(experiment)
        self.event = event
        self._results = results
        self.calls = []

    def warehouses(self):
        if self.warehouse is None:
            return []
        return [{"name": "Flagsmith", "status": self.warehouse, "total_events_received": 3}]

    def feature(self, name):
        return self._feature if self._feature and self._feature["name"] == name else None

    def experiment_for(self, flag):
        return self._experiment

    def experiment(self, experiment_id):
        return self._experiment

    def metric(self, metric_id):
        return {"id": metric_id, "definition": {"version": 1, "event": self.event}}

    def start(self, experiment_id):
        self.calls.append(("start", experiment_id))
        self._experiment["status"] = "running"

    def complete(self, experiment_id, variant_id):
        self.calls.append(("complete", experiment_id, variant_id))
        self._experiment["status"] = "completed"

    def results(self, experiment_id):
        return self._results

    def exposures(self, experiment_id):
        return {"payload": {"excluded_identities": 0}}

    def evaluate(self, flag, identity):
        return {"value": "Get early access"}

    def archive(self, flag):
        self.calls.append(("archive", flag))
