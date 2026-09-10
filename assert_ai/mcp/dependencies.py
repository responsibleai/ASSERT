# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Application dependencies supplied to MCP tools and resources."""

from __future__ import annotations

from dataclasses import dataclass

from assert_ai.core.workspace import WorkspaceService
from assert_ai.services.artifacts import ArtifactRepository
from assert_ai.services.configs import ConfigService
from assert_ai.services.curation import CurationService
from assert_ai.services.evaluations import EvaluationService
from assert_ai.services.library import LibraryService
from assert_ai.services.results import ResultRepository
from assert_ai.services.run_planning import RunPlanningService
from assert_ai.services.target_probe import TargetProbeService


@dataclass(frozen=True, slots=True)
class InspectServices:
    """Services and limits shared by inspect tools and resources."""

    workspace: WorkspaceService
    library: LibraryService
    configs: ConfigService
    results: ResultRepository
    artifacts: ArtifactRepository
    max_response_bytes: int


@dataclass(frozen=True, slots=True)
class AuthorServices:
    workspace: WorkspaceService
    configs: ConfigService
    planning: RunPlanningService
    max_response_bytes: int
    allowed_model_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProbeServices:
    workspace: WorkspaceService
    probe: TargetProbeService
    max_response_bytes: int


@dataclass(frozen=True, slots=True)
class JobServices:
    workspace: WorkspaceService
    evaluations: EvaluationService
    max_response_bytes: int


@dataclass(frozen=True, slots=True)
class CurationServices:
    workspace: WorkspaceService
    curation: CurationService
    max_response_bytes: int
