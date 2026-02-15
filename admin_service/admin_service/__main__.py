"""CLI entry point for the admin service."""

import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Wayback Proxy Admin Service")
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port to listen on (default: 8080)"
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--redis", default=None,
        help="Redis URL (overrides config file)",
    )
    args = parser.parse_args()

    import os
    os.environ["CONFIG_PATH"] = args.config
    if args.redis:
        os.environ["REDIS_URL"] = args.redis

    uvicorn.run(
        "admin_service.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
