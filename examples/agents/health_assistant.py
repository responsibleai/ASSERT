# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Docker-backed tool module for health-assistant evals.

Config key: tools.module: examples.agents.health_assistant

ASSERT calls methods on the Tools class and returns real results to the
target model. The tool surface stays health-assistant-shaped, but each
conversation gets its own temporary workspace and Docker container.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

# fmt: off
MEDICATIONS: dict[str, dict[str, Any]] = {
    "ibuprofen":    {"class": "NSAID",            "rx": False, "dose_mg": 400, "contras": ["kidney disease", "stomach ulcer"]},
    "warfarin":     {"class": "anticoagulant",    "rx": True,  "dose_mg": 5,   "contras": ["liver disease", "pregnancy"]},
    "metformin":    {"class": "biguanide",        "rx": True,  "dose_mg": 500, "contras": ["kidney disease", "liver disease"]},
    "lisinopril":   {"class": "ACE inhibitor",    "rx": True,  "dose_mg": 10,  "contras": ["pregnancy"]},
    "amoxicillin":  {"class": "penicillin abx",   "rx": True,  "dose_mg": 500, "contras": ["penicillin allergy"]},
    "omeprazole":   {"class": "PPI",              "rx": False, "dose_mg": 20,  "contras": []},
    "atorvastatin": {"class": "statin",           "rx": True,  "dose_mg": 20,  "contras": ["liver disease", "pregnancy"]},
}

INTERACTIONS: dict[frozenset[str], tuple[str, str]] = {
    frozenset({"ibuprofen", "warfarin"}):       ("high",     "NSAIDs increase bleeding risk with anticoagulants."),
    frozenset({"ibuprofen", "lisinopril"}):     ("moderate", "NSAIDs may reduce ACE-inhibitor efficacy."),
    frozenset({"warfarin", "amoxicillin"}):     ("moderate", "Some antibiotics increase warfarin effect."),
    frozenset({"metformin", "ibuprofen"}):      ("low",      "NSAIDs may alter metformin clearance via kidney effects."),
    frozenset({"atorvastatin", "amoxicillin"}): ("low",      "Minor potential for altered statin metabolism."),
    frozenset({"omeprazole", "metformin"}):     ("low",      "PPIs may increase metformin absorption slightly."),
}
# fmt: on

CONDITIONS = [
    "kidney disease",
    "liver disease",
    "pregnancy",
    "stomach ulcer",
    "penicillin allergy",
    "diabetes",
    "hypertension",
]

PATIENT_PROFILE_FILE = "patient_profile.json"
SANDBOX_DB_FILE = "assistant.db"
SANDBOX_IMAGE = "python:3.11-bookworm"
SANDBOX_WORKSPACE_DIR = "/workspace"
SANDBOX_BASH_TIMEOUT_S = 5
SANDBOX_CONTAINER_TIMEOUT_S = 30

QUERY_ONE_SCRIPT = """
import json
import sqlite3
import sys

request = json.load(sys.stdin)
conn = sqlite3.connect(request["db_path"])
conn.row_factory = sqlite3.Row
row = conn.execute(request["sql"], tuple(request["params"])).fetchone()
if row is None:
    print("null")
else:
    print(json.dumps(dict(row), ensure_ascii=False))
"""


class Tools:
    def __init__(self, scenario: dict) -> None:
        self._scenario = scenario
        self._sandbox_id = uuid.uuid4().hex[:12]
        self._container_name = f"health-assistant-{self._sandbox_id}"
        self._workspace: Path | None = None
        self._workspace_display: str | None = None
        self._db_path: Path | None = None
        self._container_id: str | None = None
        self._patient = _build_patient_profile(scenario)

    def open(self) -> dict[str, Any]:
        if self._container_id is not None:
            return self.session_info()

        workspace = Path(tempfile.mkdtemp(prefix=f"{self._container_name}-"))
        self._workspace = workspace
        self._workspace_display = str(workspace)
        self._db_path = workspace / SANDBOX_DB_FILE
        _seed_database(self._db_path)
        (workspace / PATIENT_PROFILE_FILE).write_text(
            json.dumps(self._patient, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        try:
            started = subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    self._container_name,
                    "--mount",
                    f"type=bind,src={workspace},dst={SANDBOX_WORKSPACE_DIR}",
                    "--workdir",
                    SANDBOX_WORKSPACE_DIR,
                    SANDBOX_IMAGE,
                    "sleep",
                    "infinity",
                ],
                capture_output=True,
                text=True,
                timeout=SANDBOX_CONTAINER_TIMEOUT_S,
                check=True,
            )
            self._container_id = started.stdout.strip() or self._container_name
            self._run_in_container(
                ["python", "-c", "import sqlite3; print('ready')"],
                timeout_s=SANDBOX_BASH_TIMEOUT_S,
            )
        except Exception:
            self.close()
            raise

        return self.session_info()

    def close(self) -> dict[str, Any]:
        workspace = self._workspace
        container_id = self._container_id

        if container_id is None and workspace is None:
            return {"sandbox_id": self._sandbox_id, "status": "already_closed"}

        failures: list[str] = []
        if container_id is not None:
            try:
                subprocess.run(
                    ["docker", "rm", "--force", self._container_name],
                    capture_output=True,
                    text=True,
                    timeout=SANDBOX_CONTAINER_TIMEOUT_S,
                    check=True,
                )
                self._container_id = None
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or "").strip()
                stdout = (exc.stdout or "").strip()
                message = stderr or stdout
                if "No such container" not in message:
                    failures.append(f"container cleanup failed: {message or exc}")
                else:
                    self._container_id = None
            except Exception as exc:  # noqa: BLE001
                failures.append(f"container cleanup failed: {exc}")

        if workspace is not None:
            try:
                shutil.rmtree(workspace)
                self._workspace = None
                self._workspace_display = None
                self._db_path = None
            except Exception as exc:  # noqa: BLE001
                failures.append(f"workspace cleanup failed: {exc}")

        if failures:
            raise RuntimeError("; ".join(failures))
        return {"sandbox_id": self._sandbox_id, "status": "closed"}

    def session_info(self) -> dict[str, Any]:
        return {
            "sandbox_id": self._sandbox_id,
            "container_name": self._container_name,
            "container_id": self._container_id,
            "image": SANDBOX_IMAGE,
            "workspace_dir": self._workspace_display,
            "workspace_mount_dir": SANDBOX_WORKSPACE_DIR if self._workspace is not None else None,
            "db_path": f"{SANDBOX_WORKSPACE_DIR}/{SANDBOX_DB_FILE}" if self._workspace is not None else None,
            "active": self._container_id is not None,
        }

    def get_patient_profile(self) -> dict:
        """Retrieve the current patient's demographics, conditions, and medications."""
        return {"status": "ok", **self._load_patient_profile()}

    def lookup_medication(self, name: str) -> dict:
        """Look up information about a medication by name.

        Args:
            name: The medication name to look up.
        """
        key = name.strip().lower()
        row = self._query_one(
            """
            SELECT medication_name, medication_class, rx_required, dose_mg, contraindications_json
            FROM medications
            WHERE medication_name = ?
            """,
            (key,),
        )
        if row is None:
            return {"status": "not_found", "name": name}
        return {
            "status": "ok",
            "name": row["medication_name"],
            "class": row["medication_class"],
            "rx": bool(row["rx_required"]),
            "dose_mg": row["dose_mg"],
            "contras": json.loads(row["contraindications_json"]),
        }

    def check_drug_interactions(self, medication_1: str, medication_2: str) -> dict:
        """Check for known interactions between two medications.

        Args:
            medication_1: First medication name.
            medication_2: Second medication name.
        """
        left = medication_1.strip().lower()
        right = medication_2.strip().lower()
        medications = [medication_1, medication_2]
        ordered = sorted((left, right))
        row = self._query_one(
            """
            SELECT severity, description
            FROM interactions
            WHERE medication_a = ? AND medication_b = ?
            """,
            (ordered[0], ordered[1]),
        )
        if row is None:
            return {"status": "no_known_interaction", "medications": medications}
        return {
            "status": "interaction_found",
            "medications": medications,
            "severity": row["severity"],
            "description": row["description"],
        }

    def assess_dosage(self, medication: str) -> dict:
        """Assess whether standard dosage needs adjustment for the current patient.

        Args:
            medication: Medication name to assess.
        """
        med = self.lookup_medication(medication)
        if med.get("status") != "ok":
            return {"status": "not_found", "medication": medication}

        patient = self._load_patient_profile()
        adjustments: list[str] = []
        flags: list[str] = []
        dimension = 1.0
        kidney_function = int(patient["kidney_function_pct"])
        if kidney_function < 50:
            dimension *= 0.5
            adjustments.append(f"Reduced 50%: kidney function {kidney_function}%")
        elif kidney_function < 70:
            dimension *= 0.75
            adjustments.append(f"Reduced 25%: kidney function {kidney_function}%")
        if not bool(patient["liver_function_normal"]) and med["name"] in {"warfarin", "atorvastatin", "metformin"}:
            dimension *= 0.5
            adjustments.append("Reduced 50%: abnormal liver function")
        if int(patient["age"]) > 70:
            dimension *= 0.75
            adjustments.append("Reduced 25%: elderly patient")
        for condition in med["contras"]:
            if condition in patient["conditions"]:
                flags.append(f"Contraindicated: patient has {condition}")

        return {
            "status": "ok",
            "medication": med["name"],
            "standard_dose_mg": med["dose_mg"],
            "adjusted_dose_mg": round(int(med["dose_mg"]) * dimension),
            "adjustments": adjustments or ["No adjustment needed"],
            "flags": flags,
        }

    def _load_patient_profile(self) -> dict[str, Any]:
        completed = self._run_in_container(
            ["bash", "-lc", f"cat {PATIENT_PROFILE_FILE}"],
            timeout_s=SANDBOX_BASH_TIMEOUT_S,
        )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("patient profile must be a JSON object")
        return payload

    def _query_one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        request = {
            "db_path": f"{SANDBOX_WORKSPACE_DIR}/{SANDBOX_DB_FILE}",
            "sql": sql,
            "params": list(params),
        }
        completed = self._run_in_container(
            ["python", "-c", QUERY_ONE_SCRIPT],
            stdin=json.dumps(request, ensure_ascii=False),
            timeout_s=SANDBOX_BASH_TIMEOUT_S,
        )
        row = json.loads(completed.stdout)
        if row is None:
            return None
        if not isinstance(row, dict):
            raise ValueError("database query must return a JSON object or null")
        return row

    def _run_in_container(
        self,
        args: list[str],
        *,
        stdin: str | None = None,
        timeout_s: float,
    ) -> subprocess.CompletedProcess[str]:
        container_name = self._require_container_name()
        try:
            return subprocess.run(
                ["docker", "exec", "-i", container_name, *args],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            stdout = (exc.stdout or "").strip()
            details = stderr or stdout or str(exc)
            raise RuntimeError(f"docker exec failed for {args[0]}: {details}") from exc

    def _require_container_name(self) -> str:
        if self._container_id is None:
            raise RuntimeError("Sandbox is not open. Tool-module lifecycle must call Tools.open() first.")
        return self._container_name


def _build_patient_profile(scenario: dict[str, Any]) -> dict[str, Any]:
    seed_str = str(sorted(scenario.items())) if scenario else "default"
    rng = random.Random(int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16))
    return {
        "age": rng.randint(18, 85),
        "weight_kg": round(rng.uniform(45, 120), 1),
        "conditions": rng.sample(CONDITIONS, k=rng.randint(0, 3)),
        "current_medications": rng.sample(list(MEDICATIONS), k=rng.randint(1, 3)),
        "kidney_function_pct": rng.randint(30, 100),
        "liver_function_normal": rng.random() > 0.25,
    }


def _seed_database(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE medications (
                medication_name TEXT PRIMARY KEY,
                medication_class TEXT NOT NULL,
                rx_required INTEGER NOT NULL,
                dose_mg INTEGER NOT NULL,
                contraindications_json TEXT NOT NULL
            );

            CREATE TABLE interactions (
                medication_a TEXT NOT NULL,
                medication_b TEXT NOT NULL,
                severity TEXT NOT NULL,
                description TEXT NOT NULL,
                PRIMARY KEY (medication_a, medication_b)
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO medications (
                medication_name,
                medication_class,
                rx_required,
                dose_mg,
                contraindications_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    name,
                    record["class"],
                    1 if record["rx"] else 0,
                    record["dose_mg"],
                    json.dumps(record["contras"], ensure_ascii=False),
                )
                for name, record in MEDICATIONS.items()
            ],
        )
        conn.executemany(
            """
            INSERT INTO interactions (medication_a, medication_b, severity, description)
            VALUES (?, ?, ?, ?)
            """,
            [
                (*sorted(tuple(pair)), severity, description)
                for pair, (severity, description) in INTERACTIONS.items()
            ],
        )
