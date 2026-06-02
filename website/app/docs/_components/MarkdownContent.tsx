"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeRaw from "rehype-raw";
import rehypeSlug from "rehype-slug";
import "highlight.js/styles/github-dark.css";

// Repo-relative asset references in the source markdown (e.g. `../assets/foo.png`)
// must be rewritten to URLs that resolve under the site basePath.
const BASE_PATH = "/ASSERT";

function rewriteAssetPaths(source: string): string {
	// Matches `./assets/` or any depth of `../assets/` after a quote or paren
	return source.replace(/(["'(])(?:\.\.\/)+assets\//g, `$1${BASE_PATH}/assets/`)
		.replace(/(["'(])\.\/assets\//g, `$1${BASE_PATH}/assets/`);
}

export default function MarkdownContent({ source }: { source: string }) {
	return (
		<div className="docs-prose">
			<ReactMarkdown
				remarkPlugins={[remarkGfm]}
				rehypePlugins={[rehypeRaw, rehypeSlug, rehypeHighlight]}
			>
				{rewriteAssetPaths(source)}
			</ReactMarkdown>
		</div>
	);
}
