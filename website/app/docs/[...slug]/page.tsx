import { redirect } from "next/navigation";
import DocsSidebar from "../_components/DocsSidebar";
import MarkdownContent from "../_components/MarkdownContent";
import OnThisPage from "../_components/OnThisPage";
import {
	getAllDocs,
	getDocBySlug,
	getDocGroupLabel,
	getHeadings,
} from "../_lib/docs";

type Params = { slug: string[] };

export async function generateStaticParams(): Promise<Params[]> {
	return getAllDocs().map((doc) => ({ slug: doc.slug }));
}

export async function generateMetadata({ params }: { params: Promise<Params> }) {
	const { slug } = await params;
	const doc = getDocBySlug(slug);
	if (!doc) return { title: "Not found · ASSERT Docs" };
	return {
		title: `${doc.title} · ASSERT Docs`,
		description: doc.description,
	};
}

export default async function DocPage({ params }: { params: Promise<Params> }) {
	const { slug } = await params;
	// Legacy URL: README is now the docs index.
	if (slug.length === 1 && slug[0].toLowerCase() === "readme") {
		redirect("/docs");
	}
	const doc = getDocBySlug(slug);
	if (!doc) return null;
	const headings = getHeadings(doc.content);
	const groupLabel = getDocGroupLabel(doc);
	return (
		<div className="docs-layout has-toc">
			<DocsSidebar activeHref={doc.href} />
			<main className="docs-main">
				<article className="docs-article">
					<header className="docs-article-header">
						{groupLabel && (
							<nav className="docs-breadcrumbs" aria-label="Breadcrumb">
								<ol>
									<li>{groupLabel}</li>
									<li className="docs-breadcrumbs-sep" aria-hidden="true">/</li>
									<li className="is-current">{doc.title}</li>
								</ol>
							</nav>
						)}
						<h1 className="docs-title">{doc.title}</h1>
						{doc.description && <p className="docs-lede">{doc.description}</p>}
					</header>
					<MarkdownContent source={doc.content} />
				</article>
			</main>
			<OnThisPage headings={headings} />
		</div>
	);
}
