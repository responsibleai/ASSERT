import importlib.util
from pathlib import Path


def load_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "personal"
        / "ahmedmagooda"
        / "template-generation-automation"
        / "summarize_run_prefixes.py"
    )
    spec = importlib.util.spec_from_file_location("summarize_run_prefixes", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_select_metrics_excludes_requested_keys():
    module = load_module()

    selected = module.select_metrics(module.METRICS, ["coverage_score", "relevance_mean"])

    assert [metric.key for metric in selected] == [
        "mean_harm_level",
        "llm_pair_diversity",
        "global_dimensions",
        "global_unique_dimensions",
        "relevant_unique_dimensions",
    ]
