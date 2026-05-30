import { getDocsNav } from "../_lib/docs";
import DocsSidebarClient from "./DocsSidebarClient";

export default function DocsSidebar({ activeHref }: { activeHref?: string }) {
	const nav = getDocsNav();
	return <DocsSidebarClient nav={nav} activeHref={activeHref} />;
}
