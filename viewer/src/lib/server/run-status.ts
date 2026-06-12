// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { listSubdirectories, readJsonFile, isSafeArtifactId, runDirPath, RUN_MANIFEST_FILE } from './artifacts.js';
import { ARTIFACTS_ROOT } from './config.js';
import type { Manifest } from '$lib/types.js';

type UiStageStatus = 'pending' | 'running' | 'completed' | 'skipped' | 'error';

// A run is considered abandoned (process died without writing a terminal
// status) if its manifest still says "running" but the heartbeat is older
// than this threshold. The runner refreshes heartbeat_at on every
// _write_manifest call, which currently fires at every stage transition.
const ABANDONED_THRESHOLD_MS = 5 * 60 * 1000;

export interface PersistedRunState {
	status: 'running' | 'completed' | 'failed' | 'abandoned';
	currentStage: string | null;
	stages: Record<string, UiStageStatus>;
	exitCode: number | null;
	startedAt: string | null;
	heartbeatAt: string | null;
}

export interface RunStatusPayload extends PersistedRunState {
	suiteId: string;
	runId: string;
}

function toUiStageStatus(status: unknown): UiStageStatus {
	if (status === 'running') return 'running';
	if (status === 'completed') return 'completed';
	if (status === 'skipped') return 'skipped';
	if (status === 'failed' || status === 'error') return 'error';
	return 'pending';
}

function normalizeStages(raw: unknown): Record<string, UiStageStatus> {
	if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
	return Object.fromEntries(
		Object.entries(raw as Record<string, unknown>).map(([name, value]) => [name, toUiStageStatus(value)])
	);
}

function manifestStartedAt(value: unknown): string | null {
	if (typeof value === 'number') return new Date(value * 1000).toISOString();
	return typeof value === 'string' ? value : null;
}

function isHeartbeatStale(heartbeatAt: string | null, now: number): boolean {
	if (!heartbeatAt) return false;
	const parsed = Date.parse(heartbeatAt);
	if (Number.isNaN(parsed)) return false;
	return now - parsed > ABANDONED_THRESHOLD_MS;
}

function loadManifest(suiteId: string, runId: string): Manifest | null {
	return readJsonFile<Manifest>(`${runDirPath(suiteId, runId)}/${RUN_MANIFEST_FILE}`, { missingOk: true });
}

function buildPersistedRunState(manifest: Manifest | null): PersistedRunState | null {
	if (!manifest) return null;

	let status: PersistedRunState['status'] = 'running';
	if (manifest.status === 'running' || manifest.status === 'completed' || manifest.status === 'failed') {
		status = manifest.status;
	}

	const heartbeatAt = typeof manifest.heartbeat_at === 'string' ? manifest.heartbeat_at : null;

	// If the manifest claims "running" but the heartbeat is stale, the run
	// process is gone. Surface that distinctly so callers can render it as
	// abandoned instead of misleading the user with an active state.
	if (status === 'running' && isHeartbeatStale(heartbeatAt, Date.now())) {
		status = 'abandoned';
	}

	const stages = normalizeStages(manifest.stages);
	const currentStage = Object.entries(stages).find(([, value]) => value === 'running')?.[0] ?? null;

	return {
		status,
		currentStage,
		stages,
		exitCode: status === 'running' ? null : status === 'completed' ? 0 : 1,
		startedAt: manifestStartedAt(manifest.started_at),
		heartbeatAt
	};
}

export function loadPersistedRunState(suiteId: string, runId: string): PersistedRunState | null {
	if (!isSafeArtifactId(suiteId) || !isSafeArtifactId(runId)) return null;
	return buildPersistedRunState(loadManifest(suiteId, runId));
}

export function loadRunStatusPayload(suiteId: string, runId: string): RunStatusPayload | null {
	const persisted = loadPersistedRunState(suiteId, runId);
	if (!persisted) return null;
	return { suiteId, runId, ...persisted };
}

export function getActiveRuns(): RunStatusPayload[] {
	const runs: RunStatusPayload[] = [];

	for (const suiteId of listSubdirectories(ARTIFACTS_ROOT)) {
		for (const runId of listSubdirectories(`${ARTIFACTS_ROOT}/${suiteId}`)) {
			const statusPayload = loadRunStatusPayload(suiteId, runId);
			if (!statusPayload || statusPayload.status !== 'running') continue;
			runs.push(statusPayload);
		}
	}

	return runs.sort((left, right) => {
		if (!left.startedAt && !right.startedAt) return 0;
		if (!left.startedAt) return 1;
		if (!right.startedAt) return -1;
		return right.startedAt.localeCompare(left.startedAt);
	});
}
