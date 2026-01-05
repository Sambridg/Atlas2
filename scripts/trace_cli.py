import argparse
from pathlib import Path

from caal.trace_store import TraceStore


def parse_args():
    parser = argparse.ArgumentParser(description="Inspect CAAL trace store")
    parser.add_argument("--list", action="store_true", help="List rounds")
    parser.add_argument("--round", type=str, help="Fetch a round and its events")
    parser.add_argument("--export", type=str, help="Export all traces to a JSONL path")
    parser.add_argument("--db", type=str, default="data/traces.db", help="Path to traces.db")
    return parser.parse_args()


def main():
    args = parse_args()
    store = TraceStore(args.db)

    if args.list:
        # naive list of round ids
        rounds = store._conn.execute(
            "SELECT round_id, conversation_id, round_seq, status, created_at FROM rounds ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        for row in rounds:
            print(f"{row['round_id']} convo={row['conversation_id']} seq={row['round_seq']} status={row['status']}")
        return

    if args.round:
        header = store.fetch_round(args.round)
        if not header:
            print("Round not found")
            return
        print("Round header:")
        for k, v in header.items():
            print(f"  {k}: {v}")
        print("Events:")
        for ev in store.fetch_events(args.round):
            print(f"  #{ev['event_seq']} {ev['event_type']} status={ev['status']} payload={ev['payload']}")
        return

    if args.export:
        path = store.export_jsonl(args.export)
        print(f"Exported to {path}")
        return

    print("No action specified. Use --help.")


if __name__ == "__main__":
    main()
