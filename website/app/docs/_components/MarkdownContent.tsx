"use client";

import type { AnchorHTMLAttributes } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeRaw from "rehype-raw";
import rehypeSlug from "rehype-slug";
import "highlight.js/styles/github-dark.css";

// Repo-relative asset references in the source markdown (e.g. `../assets/foo.png`)
// must be rewritten to URLs that resolve under the site basePath.
const BASE_PATH = "/ASSERT";

// GitHub blob URL used when a markdown link escapes the docs/ tree
// (e.g. `../examples/README.md`). Matches the "GitHub repo" link in
// app/page.tsx and TopNav.tsx so the source-of-truth repo is consistent.
const GITHUB_BLOB_BASE =
	process.env.NEXT_PUBLIC_DOCS_GITHUB_BLOB_BASE ??
	"https://github.com/microsoft/ASSERT/blob/main";

function rewriteAssetPaths(source: string): string {
	// Matches `./assets/` or any depth of `../assets/` after a quote or paren
	return source.replace(/(["'(])(?:\.\.\/)+assets\//g, `$1${BASE_PATH}/assets/`)
		.replace(/(["'(])\.\/assets\//g, `$1${BASE_PATH}/assets/`);
}

// Resolve a POSIX-style relative path against a base directory. Unlike
// `node:path.resolve`, an ascent (`..`) above the base is preserved in the
// output (as a leading `..` segment) so callers can detect that the link
// escaped the docs tree.
function resolveRelativePath(baseDir: string, target: string): string {
	const segments: string[] = [];
	const all = [...baseDir.split("/"), ...target.split("/")];
	for (const segment of all) {
		if (segment === "" || segment === ".") continue;
		if (segment === "..") {
			if (segments.length > 0 && segments[segments.length - 1] !== "..") {
				segments.pop();
			} else {
				segments.push("..");
			}
			continue;
		}
		segments.push(segment);
	}
	return segments.join("/");
}

// Split `path.md#anchor?query` into pieces so we can rewrite just the path part.
function splitLinkHref(href: string): { path: string; suffix: string } {
	const hashIdx = href.indexOf("#");
	const queryIdx = href.indexOf("?");
	const cutAt = [hashIdx, queryIdx].filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? -1;
	if (cutAt === -1) return { path: href, suffix: "" };
	return { path: href.slice(0, cutAt), suffix: href.slice(cutAt) };
}

// Is this an "external-ish" link we should leave alone?
function isExternalHref(href: string): boolean {
	return (
		href.startsWith("http://") ||
		href.startsWith("https://") ||
		href.startsWith("//") ||
		href.startsWith("mailto:") ||
		href.startsWith("tel:") ||
		href.startsWith("#") ||
		href.startsWith("/")
	);
}

// Convert a doc-relative markdown link into a site URL.
//
// `currentRelativePath` is the source doc's path relative to the docs root
// (e.g. "targets/README.md" or "getting-started.md"). Top-level docs use "".
//
// Returns null if the href should be left untouched.
function rewriteMarkdownLink(
	href: string,
	currentRelativePath: string,
): string | null {
	if (!href || isExternalHref(href)) return null;
	const { path: rawPath, suffix } = splitLinkHref(href);
	if (!/\.(md|mdx)$/i.test(rawPath)) return null;

	// Normalize Windows-style separators just in case (the data layer already
	// does this, but be defensive: a bad currentRelativePath here would produce
	// flattened URLs that 404).
	const normalizedCurrent = currentRelativePath.replace(/\\/g, "/");

	// Source doc's directory relative to docs/ root.
	const lastSlash = normalizedCurrent.lastIndexOf("/");
	const baseDir = lastSlash >= 0 ? normalizedCurrent.slice(0, lastSlash) : "";

	const resolved = resolveRelativePath(baseDir, rawPath);

	// Anything that climbed above the docs/ root (e.g. ../examples/README.md)
	// is repo content we do not publish on the docs site. Send those to GitHub
	// so the link is at least live and points at the canonical source.
	if (resolved.startsWith("../") || resolved === "..") {
		const fromRepoRoot = resolveRelativePath(
			"docs/" + baseDir,
			rawPath,
		);
		return `${GITHUB_BLOB_BASE}/${fromRepoRoot}${suffix}`;
	}

	// Strip extension, lowercase the slug to match how docs.ts slugs files.
	const noExt = resolved.replace(/\.(md|mdx)$/i, "").toLowerCase();

	// Top-level README is the docs index. Nested READMEs render as their own
	// `/readme/` page (see app/docs/[...slug]/page.tsx and docs.ts).
	if (noExt === "readme") return `${BASE_PATH}/docs/${suffix}`;

	return `${BASE_PATH}/docs/${noExt}/${suffix}`;
}

type MarkdownContentProps = {
	source: string;
	// Source file's path relative to the docs root, e.g. "targets/README.md"
	// or "getting-started.md". Empty string for the top-level docs index.
	relativePath?: string;
};

export default function MarkdownContent({ source, relativePath = "" }: MarkdownContentProps) {
	const anchorRenderer = ({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
		const rewritten = href ? rewriteMarkdownLink(href, relativePath) : null;
		return (
			<a href={rewritten ?? href} {...rest}>
				{children}
			</a>
		);
	};
	return (
		<div className="docs-prose">
			<ReactMarkdown
				remarkPlugins={[remarkGfm]}
				rehypePlugins={[rehypeRaw, rehypeSlug, rehypeHighlight]}
				components={{ a: anchorRenderer }}
			>
				{rewriteAssetPaths(source)}
			</ReactMarkdown>
		</div>
	);
}
