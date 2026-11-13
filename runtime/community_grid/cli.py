"""Command line entrypoint for first-boot inspection."""
import argparse
import json
from .hardware import discover
from .models import recommend

def main():
    parser = argparse.ArgumentParser(description="Community Grid OS")
    parser.add_argument("command", choices=["hardware", "recommend", "health"])
    args = parser.parse_args()
    if args.command == "hardware":
        data = discover()
    elif args.command == "recommend":
        data = recommend(discover())
    else:
        data = {"status": "ok", "mode": "standalone", "version": "0.1.0"}
    print(json.dumps(data, indent=2))

if __name__ == "__main__":
    main()
