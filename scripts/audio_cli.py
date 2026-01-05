import argparse
from pathlib import Path

from caal.audio_store import AudioStore


def parse_args():
    parser = argparse.ArgumentParser(description="Inspect and clean CAAL audio artifacts")
    parser.add_argument("--list", action="store_true", help="List recent audio artifacts")
    parser.add_argument("--show", type=str, help="Show metadata for a specific audio_id")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup expired, unpinned artifacts")
    parser.add_argument("--db", type=str, default="data/audio.db", help="Path to audio.db")
    return parser.parse_args()


def main():
    args = parse_args()
    store = AudioStore(Path(args.db))

    if args.list:
        for meta in store.list_artifacts():
            print(f"{meta['audio_id']} pinned={bool(meta['pinned'])} path={meta['path']} created={meta['created_at']}")
        return

    if args.show:
        meta = store.get_artifact(args.show)
        if not meta:
            print("Not found")
            return
        for k, v in meta.items():
            print(f"{k}: {v}")
        return

    if args.cleanup:
        deleted = store.cleanup_expired()
        print(f"Deleted {deleted} expired artifacts")
        return

    print("No action specified. Use --help.")


if __name__ == "__main__":
    main()
