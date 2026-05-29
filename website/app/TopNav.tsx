"use client";

import Image from "next/image";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const BASE_PATH = "/ASSERT";

function GitHubMark() {
	return (
		<svg width="18" height="18" viewBox="0 0 16 16" fill="none" aria-hidden="true">
			<path
				d="M8 0.58C3.9 0.58 0.58 3.9 0.58 8C0.58 11.27 2.69 14.05 5.62 15.03C5.99 15.1 6.12 14.87 6.12 14.68C6.12 14.52 6.11 14.02 6.11 13.48C4.09 13.85 3.57 12.99 3.41 12.53C3.32 12.3 2.95 11.59 2.64 11.41C2.39 11.27 2.03 10.93 2.63 10.92C3.19 10.91 3.59 11.43 3.72 11.66C4.36 12.74 5.37 12.43 6.14 12.24C6.2 11.78 6.39 11.47 6.6 11.29C4.81 11.09 2.94 10.4 2.94 7.35C2.94 6.48 3.24 5.78 3.76 5.24C3.69 5.06 3.42 4.31 3.84 3.3C3.84 3.3 4.46 3.11 6.11 4.22C6.71 4.05 7.35 3.97 8 3.97C8.65 3.97 9.29 4.05 9.89 4.22C11.54 3.1 12.16 3.3 12.16 3.3C12.58 4.31 12.31 5.06 12.24 5.24C12.76 5.78 13.06 6.47 13.06 7.35C13.06 10.41 11.18 11.09 9.39 11.29C9.66 11.52 9.9 11.95 9.9 12.62C9.9 13.58 9.89 14.36 9.89 14.68C9.89 14.87 10.02 15.1 10.39 15.03C13.31 14.05 15.42 11.26 15.42 8C15.42 3.9 12.1 0.58 8 0.58Z"
				fill="currentColor"
			/>
		</svg>
	);
}

export default function TopNav() {
	const [isMenuOpen, setIsMenuOpen] = useState(false);
	const pathname = usePathname() || "";
	const inDocs = pathname.startsWith("/docs") || pathname.startsWith(`${BASE_PATH}/docs`);

	useEffect(() => {
		if (!isMenuOpen) return;
		const onKeyDown = (event: KeyboardEvent) => {
			if (event.key === "Escape") setIsMenuOpen(false);
		};
		window.addEventListener("keydown", onKeyDown);
		return () => window.removeEventListener("keydown", onKeyDown);
	}, [isMenuOpen]);

	return (
		<div className="top-nav-wrap">
			<nav className="top-nav" aria-label="Primary">
				<div className="nav-left">
					<a href={`${BASE_PATH}/`} className="brand-mark" aria-label="Assert home">
						<Image
							src={`${BASE_PATH}/icons/Logo.svg`}
							alt=""
							width={18}
							height={18}
							className="brand-logo"
						/>
						<span className="brand-word">ASSERT.</span>
					</a>
					{inDocs && (
						<>
							<span className="brand-sep" aria-hidden="true">/</span>
							<a href={`${BASE_PATH}/docs`} className="brand-context">Documentation</a>
						</>
					)}
				</div>

				<button
					type="button"
					className={`nav-menu-toggle${isMenuOpen ? " is-open" : ""}`}
					onClick={() => setIsMenuOpen((prev) => !prev)}
					aria-label="Toggle navigation menu"
					aria-expanded={isMenuOpen}
					aria-controls="top-nav-actions"
				>
					<span className="nav-menu-toggle-bar" />
					<span className="nav-menu-toggle-bar" />
					<span className="nav-menu-toggle-bar" />
				</button>

				<div id="top-nav-actions" className={`nav-actions${isMenuOpen ? " is-open" : ""}`}>
					<a
						href="https://github.com/microsoft/ASSERT/"
						target="_blank"
						rel="noopener noreferrer"
						className="nav-link"
						onClick={() => setIsMenuOpen(false)}
					>
						<span>GitHub</span>
						<GitHubMark />
					</a>
					{!inDocs && (
						<a href={`${BASE_PATH}/docs`} className="nav-btn nav-btn-secondary" onClick={() => setIsMenuOpen(false)}>
							Documentation
						</a>
					)}
					<a
						href="https://aka.ms/assert"
						target="_blank"
						rel="noopener noreferrer"
						className="nav-btn nav-btn-secondary"
						onClick={() => setIsMenuOpen(false)}
					>
						Read the blog
					</a>
					<a href="#" className="nav-btn nav-btn-shine" onClick={() => setIsMenuOpen(false)}>
						<span className="nav-btn-shine-border" aria-hidden="true" />
						<span className="nav-btn-shine-label">Get started</span>
					</a>
				</div>
			</nav>
		</div>
	);
}
