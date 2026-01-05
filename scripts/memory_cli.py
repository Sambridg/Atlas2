import argparse
from pathlib import Path

from caal.memory_store import MemoryStore


def parse_args():
    parser = argparse.ArgumentParser(description="Inspect CAAL memory store")
    parser.add_argument("--list", action="store_true", help="List memory buckets")
    parser.add_argument("--bucket", type=str, help="Show details for a bucket")
    parser.add_argument("--note", nargs=2, metavar=("BUCKET", "NOTE"), help="Append a note to a bucket")
    parser.add_argument("--context", type=str, help="Show cached context package for a bucket")
    parser.add_argument(
        "--clear", type=str, metavar="BUCKET", help="Clear the bucket contents (destructive)"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    store = MemoryStore(path=Path("data/memory_store.db"))

    if args.list:
        for bucket in store.list_buckets():
            print(bucket)
        return

    if args.bucket:
        details = store.get_bucket_details(args.bucket)
        if details is None:
            print("Bucket not found.")
            return
        print(f"Summary: {details['summary']}")
        print("Recent entries:")
        for entry in details["recent_entries"]:
            print(f" - {entry['speaker']}: {entry['content']}")
        return

    if args.context:
        ctx = store.get_context_package(args.context)
        print(f"Bucket: {ctx['bucket_id']}")
        print(f"Register: {ctx['register_summary']}")
        print("Short context:")
        print(ctx["short_context"])
        if ctx.get("long_context"):
            print("Long context:")
            print(ctx["long_context"])
        print("Items:")
        for item in ctx["items"]:
            pin = "PIN " if item["pinned"] else ""
            print(f" - {pin}{item['item_id']}: {item['content']} (score={item['score']})")
        return

    if args.note:
        bucket, note = args.note
        store.append_note(bucket, note)
        print(f"Added note to {bucket}.")
        return

    if args.clear:
        store.clear_bucket(args.clear)
        print(f"Cleared bucket {args.clear}.")
        return

    print("No action specified. Use --help.")


if __name__ == "__main__":
    main()
