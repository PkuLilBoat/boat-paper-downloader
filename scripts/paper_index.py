"""CLI for inspecting the SQLite-backed paper registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from paper_registry import (
        REGISTRY_DB,
        REGISTRY_JSON,
        backfill_from_session_logs,
        export_collection_json,
        export_collection_markdown,
        find_registry_entries,
        import_failure_manifest,
        import_collection_markdown,
        list_collections,
        load_registry,
        registry_stats,
    )
except ImportError:  # pragma: no cover - direct script fallback
    from .paper_registry import (  # type: ignore
        REGISTRY_DB,
        REGISTRY_JSON,
        backfill_from_session_logs,
        export_collection_json,
        export_collection_markdown,
        find_registry_entries,
        import_failure_manifest,
        import_collection_markdown,
        list_collections,
        load_registry,
        registry_stats,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect the paper-downloader SQLite registry")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("stats", help="Show registry counts by status and source method")

    find_parser = subparsers.add_parser("find", help="Search the registry")
    find_parser.add_argument("--query", help="Free-text query across title/author/journal/doi")
    find_parser.add_argument("--doi", help="Exact DOI match")
    find_parser.add_argument("--status", help="Exact status match")
    find_parser.add_argument("--limit", type=int, default=10, help="Maximum rows to print")

    export_parser = subparsers.add_parser("export-json", help="Rewrite the JSON snapshot from SQLite")
    export_parser.add_argument("--output", help=f"Custom output path (default: {REGISTRY_JSON})")

    subparsers.add_parser(
        "backfill-session-logs",
        help="Import DOI/title/status rows from existing session markdown logs into SQLite",
    )

    failure_parser = subparsers.add_parser(
        "import-failure-manifest",
        help="Import failed/manual download rows from a JSON manifest into the retry registry",
    )
    failure_parser.add_argument("path", help="JSON manifest path")
    failure_parser.add_argument("--collection", help="Collection/retry-queue name; defaults to the manifest filename")
    failure_parser.add_argument(
        "--type",
        default="download_retry_queue",
        help="Collection type",
    )

    collection_parser = subparsers.add_parser("collection", help="Manage project/review literature collections")
    collection_subparsers = collection_parser.add_subparsers(dest="collection_command", required=True)

    collection_subparsers.add_parser("list", help="List all collections")

    import_md = collection_subparsers.add_parser("import-md", help="Import a Markdown literature list")
    import_md.add_argument("path", help="Markdown file path")
    import_md.add_argument("--name", help="Collection name; defaults to the first H1 or filename")
    import_md.add_argument("--type", default="review_library", help="Collection type")

    export_md = collection_subparsers.add_parser("export-md", help="Export a collection to Markdown")
    export_md.add_argument("name", help="Collection name")
    export_md.add_argument("--output", help="Output Markdown path")

    export_json = collection_subparsers.add_parser("export-json", help="Export a collection to JSON")
    export_json.add_argument("name", help="Collection name")
    export_json.add_argument("--output", help="Output JSON path")
    return parser.parse_args()


def _print_find_results(results: list[dict]) -> None:
    if not results:
        print("No matching papers.")
        return
    for item in results:
        path = item.get("final_path") or item.get("raw_path") or "-"
        print(f"- [{item.get('status', '-')}] {item.get('doi', '-')}")
        print(f"  title: {item.get('title', '-')}")
        print(f"  author/year: {item.get('first_author', '-')} / {item.get('year', '-')}")
        print(f"  method: {item.get('source_method', '-')}")
        print(f"  path: {path}")


def main() -> int:
    args = parse_args()

    if args.command == "stats":
        stats = registry_stats()
        print(f"Database: {stats['database']}")
        print(f"Total papers: {stats['total']}")
        print("By status:")
        for key, value in stats["by_status"].items():
            print(f"  - {key}: {value}")
        print("By method:")
        for key, value in stats["by_method"].items():
            print(f"  - {key}: {value}")
        return 0

    if args.command == "find":
        results = find_registry_entries(
            query=args.query,
            doi=args.doi,
            status=args.status,
            limit=args.limit,
        )
        _print_find_results(results)
        return 0

    if args.command == "export-json":
        registry = load_registry()
        output = Path(args.output).expanduser().resolve() if args.output else REGISTRY_JSON
        output.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Exported {len(registry.get('papers', []))} records to {output}")
        return 0

    if args.command == "backfill-session-logs":
        result = backfill_from_session_logs()
        print(
            f"Scanned {result['sessions']} session logs, imported {result['imported']} rows, skipped {result['skipped']} rows."
        )
        return 0

    if args.command == "import-failure-manifest":
        result = import_failure_manifest(args.path, collection_name=args.collection, collection_type=args.type)
        print(
            f"Imported {result['imported']} failed/manual rows into retry collection: {result['collection']} "
            f"(skipped {result['skipped']})"
        )
        print(f"Run archive: {result['run_archive']}")
        return 0

    if args.command == "collection":
        if args.collection_command == "list":
            collections = list_collections()
            if not collections:
                print("No collections.")
                return 0
            for item in collections:
                print(f"- {item['name']} ({item['type']}): {item['item_count']} items")
                if item.get("source_path"):
                    print(f"  source: {item['source_path']}")
            return 0

        if args.collection_command == "import-md":
            result = import_collection_markdown(args.path, name=args.name, collection_type=args.type)
            print(f"Imported {result['imported']} items into collection: {result['collection']}")
            return 0

        if args.collection_command == "export-md":
            rendered = export_collection_markdown(args.name, output_path=args.output)
            if args.output:
                print(f"Exported Markdown collection to {Path(args.output).expanduser().resolve()}")
            else:
                print(rendered)
            return 0

        if args.collection_command == "export-json":
            collection = export_collection_json(args.name, output_path=args.output)
            if args.output:
                print(f"Exported JSON collection to {Path(args.output).expanduser().resolve()}")
            else:
                print(json.dumps(collection, ensure_ascii=False, indent=2))
            return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
