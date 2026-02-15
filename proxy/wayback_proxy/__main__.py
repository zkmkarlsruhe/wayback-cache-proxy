"""CLI entry point for Wayback Proxy."""

import asyncio
import argparse

from .config import Config
from .throttle import SPEED_TIERS
from .server import run_proxy


def main():
    parser = argparse.ArgumentParser(description="Wayback Machine HTTP Proxy")
    parser.add_argument(
        "--config", default=None, help="Path to YAML config file"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8888, help="Port to listen on (default: 8888)"
    )
    parser.add_argument(
        "--date", default="20010101", help="Target date YYYYMMDD (default: 20010101)"
    )
    parser.add_argument(
        "--redis", default="redis://localhost:6379/0", help="Redis URL"
    )
    parser.add_argument(
        "--allowlist", action="store_true", help="Enable allowlist mode"
    )
    parser.add_argument(
        "--error-pages", default="", help="Path to custom error page templates directory"
    )

    # Throttle
    parser.add_argument(
        "--speed",
        default=None,
        choices=list(SPEED_TIERS.keys()),
        help="Default throttle speed (default: none/unlimited)",
    )
    parser.add_argument(
        "--speed-selector",
        action="store_true",
        help="Allow users to pick speed via header bar dropdown",
    )

    # Header bar
    parser.add_argument(
        "--header-bar", action="store_true", help="Enable the header bar overlay"
    )
    parser.add_argument(
        "--header-bar-position",
        default=None,
        choices=["top", "bottom"],
        help="Header bar position (default: top)",
    )
    parser.add_argument(
        "--header-bar-text", default=None, help="Custom branding text in header bar"
    )

    # Landing page
    parser.add_argument(
        "--landing-page-dir", default=None, help="Path to landing page template directory"
    )
    parser.add_argument(
        "--no-landing-page", action="store_true", help="Disable the landing page"
    )

    # Admin / crawler
    parser.add_argument(
        "--admin", action="store_true", help="Enable admin interface at /_admin/"
    )
    parser.add_argument(
        "--admin-password", default=None, help="Password for admin Basic Auth (empty = no auth)"
    )
    parser.add_argument(
        "--crawl-concurrency", type=int, default=None,
        help="Max parallel fetches during crawl (default: 3)",
    )
    parser.add_argument(
        "--crawl-max-urls", type=int, default=None,
        help="Max URLs to visit per crawl (default: 10000, 0=unlimited)",
    )

    args = parser.parse_args()

    # Build config: YAML file → env vars → CLI args (highest priority)
    if args.config:
        config = Config.from_yaml(args.config)
    else:
        config = Config.from_env()

    # Override with CLI args (only if explicitly provided)
    if args.host != parser.get_default("host") or not args.config:
        config.proxy.host = args.host
    if args.port != parser.get_default("port") or not args.config:
        config.proxy.port = args.port
    if args.date != parser.get_default("date") or not args.config:
        config.wayback.target_date = args.date
    if args.redis != parser.get_default("redis") or not args.config:
        config.cache.redis_url = args.redis

    if args.allowlist:
        config.access.mode = "allowlist"

    if args.error_pages:
        config.proxy.error_pages_dir = args.error_pages

    # Throttle overrides
    if args.speed is not None:
        config.throttle.default_speed = args.speed
    if args.speed_selector:
        config.throttle.allow_user_override = True

    # Header bar overrides
    if args.header_bar:
        config.header_bar.enabled = True
    if args.header_bar_position is not None:
        config.header_bar.position = args.header_bar_position
    if args.header_bar_text is not None:
        config.header_bar.custom_text = args.header_bar_text

    # Landing page overrides
    if args.landing_page_dir is not None:
        config.landing_page.template_dir = args.landing_page_dir
    if args.no_landing_page:
        config.landing_page.enabled = False

    # Admin / crawler overrides
    if args.admin:
        config.admin.enabled = True
    if args.admin_password is not None:
        config.admin.password = args.admin_password
    if args.crawl_concurrency is not None:
        config.crawler.concurrency = args.crawl_concurrency
    if args.crawl_max_urls is not None:
        config.crawler.max_urls = args.crawl_max_urls

    print("=" * 50)
    print("Wayback Proxy")
    print("=" * 50)
    print(f"Host: {config.proxy.host}")
    print(f"Port: {config.proxy.port}")
    print(f"Target Date: {config.wayback.target_date}")
    print(f"Redis: {config.cache.redis_url}")
    print(f"Access Mode: {config.access.mode}")
    if config.proxy.error_pages_dir:
        print(f"Error Pages: {config.proxy.error_pages_dir}")
    if config.throttle.default_speed != "none":
        print(f"Throttle: {config.throttle.default_speed}")
    if config.header_bar.enabled:
        print(f"Header Bar: {config.header_bar.position}")
        if config.header_bar.custom_text:
            print(f"Header Text: {config.header_bar.custom_text}")
    if config.landing_page.enabled:
        print(f"Landing Page: enabled")
    if config.admin.enabled:
        auth = "password" if config.admin.password else "open"
        print(f"Admin: enabled (auth: {auth})")
        print(f"Crawl Concurrency: {config.crawler.concurrency}")
    print("=" * 50)

    asyncio.run(run_proxy(config))


if __name__ == "__main__":
    main()
