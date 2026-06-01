import fs from "node:fs";
import path from "node:path";
import matter from "gray-matter";
import GithubSlugger from "github-slugger";

// Docs live at <repo-root>/docs, i.e. one level up from website/
export const DOCS_DIR = path.resolve(process.cwd(), "..", "docs");

export type DocSlug = string[]; // e.g. ["targets", "callable"]

export type DocMeta = {
	slug: DocSlug;
	title: string;
	description?: string;
	href: string; // "/docs/foo/bar"
	relativePath: string; // "targets/callable.md"
};

export type Doc = DocMeta & {
	content: string;
};

function isMarkdownFile(filename: string): boolean {
	return filename.toLowerCase().endsWith(".md") || filename.toLowerCase().endsWith(".mdx");
}

// Files (relative path, lowercased) that should NOT appear as their own doc
// page. README is rendered as the docs index instead.
const EXCLUDED_FILES = new Set(["readme.md", "readme.mdx"]);

function walk(dir: string, base: string = dir): string[] {
	if (!fs.existsSync(dir)) return [];
	const entries = fs.readdirSync(dir, { withFileTypes: true });
	const files: string[] = [];
	for (const entry of entries) {
		const full = path.join(dir, entry.name);
		if (entry.isDirectory()) {
			files.push(...walk(full, base));
		} else if (entry.isFile() && isMarkdownFile(entry.name)) {
			const rel = path.relative(base, full);
			if (EXCLUDED_FILES.has(rel.toLowerCase())) continue;
			files.push(rel);
		}
	}
	return files;
}

function fileToSlug(relativePath: string): DocSlug {
	// "targets/callable.md" -> ["targets", "callable"]
	// "README.md" -> ["readme"]  (skip; index lives at /docs)
	const noExt = relativePath.replace(/\.(md|mdx)$/i, "");
	return noExt.split(path.sep).map((segment) => segment.toLowerCase());
}

function titleFromContent(content: string, fallback: string): string {
	// Use first H1 if present, else fallback.
	const match = content.match(/^#\s+(.+?)\s*$/m);
	if (match) return match[1].trim();
	return fallback;
}

const ACRONYMS = new Set(["cli", "api", "ui", "url", "sdk", "id", "ai", "llm"]);

function humanize(slug: string): string {
	return slug
		.split(/[-_\s]+/)
		.filter(Boolean)
		.map((w) => {
			if (ACRONYMS.has(w.toLowerCase())) return w.toUpperCase();
			if (w.length <= 2) return w.toUpperCase();
			return w[0].toUpperCase() + w.slice(1);
		})
		.join(" ");
}

export function getAllDocs(): Doc[] {
	const files = walk(DOCS_DIR);
	const docs: Doc[] = [];
	for (const rel of files) {
		const filePath = path.join(DOCS_DIR, rel);
		const raw = fs.readFileSync(filePath, "utf8");
		const { data, content } = matter(raw);
		const slug = fileToSlug(rel);
		const lastSegment = slug[slug.length - 1];
		const title =
			(typeof data.title === "string" && data.title.trim()) ||
			titleFromContent(content, humanize(lastSegment));
		const description = typeof data.description === "string" ? data.description : undefined;
		// Strip the leading H1 from content so the page doesn't double-render it.
		const stripped = content.replace(/^\s*#\s+.+\r?\n+/, "");
		docs.push({
			slug,
			title,
			description,
			href: "/docs/" + slug.join("/"),
			relativePath: rel,
			content: stripped,
		});
	}
	// Sort: top-level files first (by priority then title), then nested by path
	const TOP_LEVEL_PRIORITY: Record<string, number> = {
		"getting-started": 0,
		"concepts": 1,
	};
	docs.sort((a, b) => {
		const ad = a.slug.length;
		const bd = b.slug.length;
		if (ad !== bd) return ad - bd;
		if (ad === 1) {
			const ap = TOP_LEVEL_PRIORITY[a.slug[0]] ?? 100;
			const bp = TOP_LEVEL_PRIORITY[b.slug[0]] ?? 100;
			if (ap !== bp) return ap - bp;
		}
		return a.slug.join("/").localeCompare(b.slug.join("/"));
	});
	return docs;
}

export function getDocBySlug(slug: DocSlug): Doc | null {
	const all = getAllDocs();
	const target = slug.join("/").toLowerCase();
	return all.find((d) => d.slug.join("/").toLowerCase() === target) ?? null;
}

export function getDocsNav(): { group: string | null; items: DocMeta[] }[] {
	const docs = getAllDocs();
	const groups = new Map<string | null, DocMeta[]>();
	for (const doc of docs) {
		const group = doc.slug.length > 1 ? doc.slug[0] : null;
		const arr = groups.get(group) ?? [];
		arr.push({
			slug: doc.slug,
			title: doc.title,
			description: doc.description,
			href: doc.href,
			relativePath: doc.relativePath,
		});
		groups.set(group, arr);
	}
	return Array.from(groups.entries()).map(([group, items]) => ({
		group: group ? humanize(group) : null,
		items,
	}));
}

export type Heading = { id: string; text: string; level: 2 };

/**
 * Extract H2 headings for a page's "On this page" TOC. Uses the same slug
 * algorithm (github-slugger) that rehype-slug uses, so IDs match the rendered DOM.
 */
export function getHeadings(content: string): Heading[] {
	const slugger = new GithubSlugger();
	const headings: Heading[] = [];
	const lines = content.split("\n");
	let inFence = false;
	for (const line of lines) {
		if (/^\s*```/.test(line)) {
			inFence = !inFence;
			continue;
		}
		if (inFence) continue;
		const m = line.match(/^(#{2})\s+(.+?)\s*$/);
		if (!m) continue;
		const level = 2 as const;
		// Strip markdown emphasis / inline code / links from the heading text.
		const text = m[2]
			.replace(/`([^`]+)`/g, "$1")
			.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
			.replace(/[*_]+/g, "")
			.trim();
		headings.push({ id: slugger.slug(text), text, level });
	}
	return headings;
}

/**
 * For breadcrumbs: returns the group label ("Targets", "Reference", etc.) for a doc,
 * or null for top-level docs.
 */
export function getDocGroupLabel(doc: DocMeta): string | null {
	return doc.slug.length > 1 ? humanize(doc.slug[0]) : null;
}

/**
 * Returns the parsed README.md from the docs root, used to render the /docs
 * landing page. Returns null if README.md does not exist.
 */
export function getDocsIndex(): { title: string; description?: string; content: string } | null {
	const candidates = ["README.md", "readme.md", "README.mdx", "readme.mdx"];
	for (const name of candidates) {
		const full = path.join(DOCS_DIR, name);
		if (!fs.existsSync(full)) continue;
		const raw = fs.readFileSync(full, "utf8");
		const { data, content } = matter(raw);
		const title =
			(typeof data.title === "string" && data.title.trim()) ||
			titleFromContent(content, "ASSERT Documentation");
		const description = typeof data.description === "string" ? data.description : undefined;
		const stripped = content.replace(/^\s*#\s+.+\r?\n+/, "");
		return { title, description, content: stripped };
	}
	return null;
}
