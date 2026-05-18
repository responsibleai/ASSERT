import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

// https://astro.build/config
export default defineConfig({
  site: "https://microsoft.github.io",
  base: "/adaptive-eval",
  trailingSlash: "always",
  integrations: [
    starlight({
      title: "Adaptive Eval",
      description:
        "Spec-driven evaluation for AI agents. Failure modes you define, test cases the pipeline generates, trace-grounded verdicts.",
      logo: {
        src: "./src/assets/logo.svg",
        replacesTitle: false,
      },
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/microsoft/adaptive-eval",
        },
      ],
      customCss: ["./src/styles/custom.css"],
      editLink: {
        baseUrl:
          "https://github.com/microsoft/adaptive-eval/edit/main/site/",
      },
      lastUpdated: true,
      pagination: true,
      tableOfContents: { minHeadingLevel: 2, maxHeadingLevel: 3 },
      sidebar: [
        {
          label: "Get Started",
          items: [
            { label: "What is Adaptive Eval", slug: "get-started/what-is" },
            { label: "Quickstart", slug: "get-started/quickstart" },
            { label: "Concepts", slug: "get-started/concepts" },
          ],
        },
        {
          label: "Learn",
          items: [
            { label: "How it works", slug: "learn/how-it-works" },
            { label: "Reading results", slug: "learn/reading-results" },
            { label: "Status & roadmap", slug: "learn/status" },
          ],
        },
        {
          label: "Author Spec",
          items: [
            { label: "Writing eval specs", slug: "author/writing-specs" },
            {
              label: "Spec vs judge dimensions",
              slug: "author/spec-vs-dimensions",
            },
          ],
        },
        {
          label: "Run & Trace",
          items: [
            { label: "Choose your target", slug: "run/targets" },
            { label: "Callable target", slug: "run/callable" },
            { label: "Model + tools target", slug: "run/model-and-tools" },
            {
              label: "Travel planner agent flow",
              slug: "run/travel-planner-flow",
            },
          ],
        },
        {
          label: "Examples",
          items: [
            { label: "Overview", slug: "examples" },
            {
              label: "Travel Planner — OTel example",
              slug: "examples/travel-planner-otel",
            },
            {
              label: "Health Assistant — prompt-agent example",
              slug: "examples/health-assistant-prompt-agent",
            },
            {
              label: "Triage Agent — eval-fix loop",
              slug: "examples/triage-agent-eval-fix",
            },
          ],
        },
        {
          label: "Reference",
          items: [
            { label: "CLI", slug: "reference/cli" },
            { label: "Risks & limitations", slug: "reference/risks" },
          ],
        },
      ],
      components: {
        Footer: "./src/components/Footer.astro",
      },
    }),
  ],
});
