"""Loads and parses workflow schemas and disposition catalogue."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from solution.config import SCHEMAS_DIR


@dataclass
class FieldDefinition:
    name: str
    type: str  # 'string', 'phone', 'date', 'decimal', 'enum'
    required: bool = False
    pii: bool = False
    reference: bool = False
    values: list[str] = field(default_factory=list)
    default: str | None = None
    aliases: list[str] = field(default_factory=list)


@dataclass
class WorkflowSchema:
    workflow_id: str
    description: str
    fields: list[FieldDefinition]

    @property
    def reference_field(self) -> FieldDefinition | None:
        for f in self.fields:
            if f.reference:
                return f
        return None


@dataclass
class Disposition:
    workflow_id: str
    code: str
    label: str
    is_terminal: bool
    is_success: bool


class SchemaCatalog:
    def __init__(self, schemas_dir: Path | None = None):
        self.schemas_dir = schemas_dir or SCHEMAS_DIR
        self.workflows: dict[str, WorkflowSchema] = {}
        self.dispositions: dict[str, dict[str, Disposition]] = {}
        self._load()

    def _load(self):
        # 1. Load workflow json schemas
        for json_file in self.schemas_dir.glob("*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            wf_id = data["workflow_id"]
            fields = []
            for fd in data.get("fields", []):
                fields.append(
                    FieldDefinition(
                        name=fd["name"],
                        type=fd["type"],
                        required=fd.get("required", False),
                        pii=fd.get("pii", False),
                        reference=fd.get("reference", False),
                        values=fd.get("values", []),
                        default=fd.get("default"),
                        aliases=fd.get("aliases", []),
                    )
                )
            self.workflows[wf_id] = WorkflowSchema(
                workflow_id=wf_id,
                description=data.get("description", ""),
                fields=fields,
            )

        # 2. Load dispositions.csv
        disp_csv = self.schemas_dir / "dispositions.csv"
        if disp_csv.exists():
            with open(disp_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    wf = row["workflow_id"].strip()
                    code = row["code"].strip()
                    label = row["label"].strip()
                    is_terminal = row["is_terminal"].strip().lower() == "true"
                    is_success = row["is_success"].strip().lower() == "true"
                    if wf not in self.dispositions:
                        self.dispositions[wf] = {}
                    self.dispositions[wf][code] = Disposition(
                        workflow_id=wf,
                        code=code,
                        label=label,
                        is_terminal=is_terminal,
                        is_success=is_success,
                    )
