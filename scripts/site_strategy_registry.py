import json
import re
from pathlib import Path


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "site_strategies.json"


def load_registry(path: Path | None = None) -> dict:
    registry_path = path or REGISTRY_PATH
    return json.loads(registry_path.read_text(encoding="utf-8"))


def site_entries(path: Path | None = None) -> list[dict]:
    return list(load_registry(path).get("site_families", []))


def build_handler_patterns(handler_field: str, path: Path | None = None) -> list[tuple[re.Pattern[str], str]]:
    patterns: list[tuple[re.Pattern[str], str]] = []
    for entry in site_entries(path):
        handler_name = entry.get(handler_field)
        if not handler_name:
            continue
        for raw_pattern in entry.get("detection_patterns", []):
            patterns.append((re.compile(raw_pattern), handler_name))
    return patterns


def build_http_handler_patterns(path: Path | None = None) -> list[tuple[re.Pattern[str], str]]:
    return build_handler_patterns("http_handler", path)


def build_playwright_handler_patterns(path: Path | None = None) -> list[tuple[re.Pattern[str], str]]:
    return build_handler_patterns("playwright_handler", path)


def detect_site_entry(url: str, path: Path | None = None) -> dict | None:
    for entry in site_entries(path):
        for raw_pattern in entry.get("detection_patterns", []):
            if re.search(raw_pattern, url):
                return entry
    return None


def markdown_table(path: Path | None = None) -> str:
    lines = [
        "| Site family | Hosts | HTTP handler | Playwright handler |",
        "| --- | --- | --- | --- |",
    ]
    for entry in site_entries(path):
        hosts = ", ".join(entry.get("hosts", []))
        lines.append(
            "| {label} | {hosts} | {http_handler} | {playwright_handler} |".format(
                label=entry["label"],
                hosts=hosts or "-",
                http_handler=entry.get("http_handler") or "-",
                playwright_handler=entry.get("playwright_handler") or "-",
            )
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(markdown_table())
