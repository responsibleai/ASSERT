import Link from "next/link";
import DocsSidebar from "./_components/DocsSidebar";
import { getDocsNav } from "./_lib/docs";

export const metadata = {
	title: "Documentation · ASSERT",
};

export default function DocsIndex() {
	const groups = getDocsNav();
	return (
		<div className="docs-layout">
			<DocsSidebar activeHref="/docs" />
			<main className="docs-main">
				<header className="docs-header">
					<h1 className="docs-title">ASSERT Documentation</h1>
					<p className="docs-lede">
						Guides, references, and examples for using ASSERT to evaluate AI systems.
					</p>
				</header>

				<div className="docs-index-grid">
					{groups.map((group, gi) => (
						<section key={group.group ?? `g-${gi}`} className="docs-index-group">
							{group.group && <h2 className="docs-index-group-title">{group.group}</h2>}
							<ul className="docs-index-list">
								{group.items.map((item) => (
									<li key={item.href}>
										<Link href={item.href} className="docs-index-card">
											<span className="docs-index-card-title">{item.title}</span>
											{item.description && (
												<span className="docs-index-card-desc">{item.description}</span>
											)}
											<span className="docs-index-card-cta">Learn more &rarr;</span>
										</Link>
									</li>
								))}
							</ul>
						</section>
					))}
				</div>
			</main>
		</div>
	);
}
