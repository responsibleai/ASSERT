// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/**
 * Model catalog loader for the viewer.
 *
 * The viewer surfaces a dropdown of model identifiers in the "new evaluation"
 * wizard. We don't ship a hard-coded list — instead we read whatever the
 * operator has configured in their environment (most commonly via .env) and
 * normalize the values into LiteLLM-style `provider/model` strings.
 *
 * The loader never throws on bad input. Missing or unparseable env vars simply
 * yield an empty catalog, in which case the wizard falls back to manual entry.
 */

type EnvRecord = Record<string, string | undefined>;

export interface ModelCatalogEntry {
	id: string;
	source: string;
}

export interface ModelCatalog {
	models: ModelCatalogEntry[];
	defaultModel?: string;
}

const LIST_ENV_KEYS = [
	'ASSERT_MODEL_OPTIONS',
	'ASSERT_MODELS',
	'LITELLM_MODEL_OPTIONS',
	'LITELLM_MODELS',
	'MODEL_OPTIONS',
	'AVAILABLE_MODELS'
] as const;

const PROVIDER_LIST_ENV_KEYS: ReadonlyArray<{ key: string; provider: string }> = [
	{ key: 'AZURE_OPENAI_DEPLOYMENTS', provider: 'azure' },
	{ key: 'AZURE_DEPLOYMENTS', provider: 'azure' },
	{ key: 'ASSERT_AZURE_DEPLOYMENTS', provider: 'azure' }
];

const SINGLE_ENV_KEYS: ReadonlyArray<{ key: string; provider?: string }> = [
	{ key: 'ASSERT_DEFAULT_MODEL' },
	{ key: 'ASSERT_MODEL' },
	{ key: 'LITELLM_MODEL' },
	{ key: 'MODEL_NAME' },
	{ key: 'OPENAI_MODEL', provider: 'openai' },
	{ key: 'OPENAI_MODEL_NAME', provider: 'openai' },
	{ key: 'OPENAI_MODEL_ID', provider: 'openai' },
	{ key: 'AZURE_OPENAI_DEPLOYMENT', provider: 'azure' },
	{ key: 'AZURE_OPENAI_DEPLOYMENT_NAME', provider: 'azure' },
	{ key: 'AZURE_OPENAI_MODEL_DEPLOYMENT', provider: 'azure' },
	{ key: 'AZURE_DEPLOYMENT', provider: 'azure' },
	{ key: 'AZURE_DEPLOYMENT_NAME', provider: 'azure' },
	{ key: 'ASSERT_AZURE_DEPLOYMENT', provider: 'azure' },
	{ key: 'ANTHROPIC_MODEL', provider: 'anthropic' },
	{ key: 'ANTHROPIC_MODEL_NAME', provider: 'anthropic' },
	{ key: 'CLAUDE_MODEL', provider: 'anthropic' },
	{ key: 'GEMINI_MODEL', provider: 'gemini' },
	{ key: 'GOOGLE_MODEL', provider: 'gemini' },
	{ key: 'VERTEXAI_MODEL', provider: 'vertex_ai' }
];

const DEFAULT_ENV_KEYS = ['ASSERT_DEFAULT_MODEL', 'LITELLM_MODEL', 'MODEL_NAME'] as const;

export function getModelCatalog(env: EnvRecord): ModelCatalog {
	const seen = new Map<string, ModelCatalogEntry>();

	const add = (rawValues: string[], source: string, provider?: string) => {
		for (const raw of rawValues) {
			const id = normalizeModelId(raw, provider);
			if (!id || seen.has(id)) continue;
			seen.set(id, { id, source });
		}
	};

	for (const key of LIST_ENV_KEYS) {
		add(parseModelValues(env[key]), key);
	}
	for (const { key, provider } of PROVIDER_LIST_ENV_KEYS) {
		add(parseModelValues(env[key]), key, provider);
	}
	for (const { key, provider } of SINGLE_ENV_KEYS) {
		add(parseModelValues(env[key]), key, provider);
	}

	let defaultModel: string | undefined;
	for (const key of DEFAULT_ENV_KEYS) {
		const candidate = normalizeModelId(firstModelValue(env[key]), undefined);
		if (candidate) {
			defaultModel = candidate;
			break;
		}
	}

	if (defaultModel && !seen.has(defaultModel)) {
		const ordered = new Map<string, ModelCatalogEntry>();
		ordered.set(defaultModel, { id: defaultModel, source: 'ASSERT_DEFAULT_MODEL' });
		for (const [id, entry] of seen) ordered.set(id, entry);
		return { models: Array.from(ordered.values()), defaultModel };
	}

	const models = Array.from(seen.values());
	if (models.length === 0) return { models: [] };

	return { models, defaultModel: defaultModel ?? models[0].id };
}

function parseModelValues(raw: string | undefined): string[] {
	const trimmed = stripWrappingQuotes(raw ?? '');
	if (!trimmed) return [];

	if (trimmed.startsWith('[')) {
		try {
			const parsed = JSON.parse(trimmed);
			if (Array.isArray(parsed)) {
				return parsed.map((item) => stripWrappingQuotes(String(item))).filter(Boolean);
			}
		} catch {
			// fall through to delimiter parsing
		}
	}

	return trimmed
		.split(/[\n,;]+/)
		.map(stripWrappingQuotes)
		.filter(Boolean);
}

function firstModelValue(raw: string | undefined): string {
	return parseModelValues(raw)[0] ?? '';
}

function normalizeModelId(raw: string, provider: string | undefined): string | null {
	const value = stripWrappingQuotes(raw);
	if (!value) return null;
	if (value.includes('/')) return value;

	const inferredProvider = provider ?? inferProvider(value);
	return inferredProvider ? `${inferredProvider}/${value}` : value;
}

function inferProvider(value: string): string | undefined {
	const lower = value.toLowerCase();
	if (/^(gpt-|o\d|text-|dall-e|tts-|whisper)/.test(lower)) return 'openai';
	if (lower.startsWith('claude')) return 'anthropic';
	if (lower.startsWith('gemini')) return 'gemini';
	return undefined;
}

function stripWrappingQuotes(value: string): string {
	const trimmed = value.trim();
	return trimmed.replace(/^['"`]+|['"`]+$/g, '').trim();
}
