"""Configuration management for Wayback Proxy."""

from dataclasses import dataclass, field, fields, asdict
from typing import Optional
import os


@dataclass
class ProxyConfig:
    """Proxy server configuration."""
    host: str = "0.0.0.0"
    port: int = 8888
    error_pages_dir: str = ""  # Path to custom error page templates


@dataclass
class WaybackConfig:
    """Wayback Machine configuration."""
    target_date: str = "20010101"  # YYYYMMDD
    date_tolerance_days: int = 365
    base_url: str = "https://web.archive.org"
    geocities_fix: bool = True  # Route geocities.com through oocities.org


@dataclass
class CacheConfig:
    """Redis cache configuration."""
    redis_url: str = "redis://localhost:6379/0"
    hot_ttl_seconds: int = 604800  # 7 days
    curated_prefix: str = "curated:"
    hot_prefix: str = "hot:"
    allowlist_key: str = "allowlist:urls"


@dataclass
class TransformConfig:
    """Content transformation configuration."""
    remove_wayback_toolbar: bool = True
    remove_wayback_scripts: bool = True
    fix_base_tags: bool = True
    fix_asset_urls: bool = True
    normalize_links: bool = True


@dataclass
class HttpsConfig:
    """HTTPS MITM configuration."""
    enabled: bool = True
    ca_cert_path: str = "/certs/ca.crt"
    ca_key_path: str = "/certs/ca.key"


@dataclass
class ThrottleConfig:
    """Bandwidth throttling configuration."""
    default_speed: str = "none"       # Key from SPEED_TIERS
    allow_user_override: bool = False # Let visitors pick speed via cookie
    cookie_name: str = "wayback_speed"


@dataclass
class LandingPageConfig:
    """Landing page configuration."""
    enabled: bool = True
    template_dir: str = ""            # Path to landing page template dir
    most_viewed_count: int = 10


@dataclass
class HeaderBarConfig:
    """Header bar overlay configuration."""
    enabled: bool = False
    position: str = "top"             # top | bottom
    custom_text: str = ""
    custom_css: str = ""
    show_speed_selector: bool = True


@dataclass
class AccessConfig:
    """Access control configuration."""
    mode: str = "open"  # open | allowlist


@dataclass
class AdminConfig:
    """Admin interface configuration."""
    enabled: bool = False
    password: str = ""  # empty = no auth required


@dataclass
class CrawlerConfig:
    """Prefetch crawler configuration."""
    concurrency: int = 3
    same_domain_only: bool = True  # for <a> links; assets always cross-domain
    max_urls: int = 10000          # cap on visited set to bound memory (0 = unlimited)


@dataclass
class Config:
    """Main configuration."""
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    wayback: WaybackConfig = field(default_factory=WaybackConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    transform: TransformConfig = field(default_factory=TransformConfig)
    https: HttpsConfig = field(default_factory=HttpsConfig)
    access: AccessConfig = field(default_factory=AccessConfig)
    throttle: ThrottleConfig = field(default_factory=ThrottleConfig)
    landing_page: LandingPageConfig = field(default_factory=LandingPageConfig)
    header_bar: HeaderBarConfig = field(default_factory=HeaderBarConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    crawler: CrawlerConfig = field(default_factory=CrawlerConfig)

    # Path to the YAML config file (not serialized, set at runtime)
    _config_path: str = field(default="", repr=False)

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        """Load config from a YAML file.

        YAML structure mirrors the dataclass hierarchy:
            proxy:
              host: "0.0.0.0"
              port: 8888
            wayback:
              target_date: "20010911"
            ...
        """
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        config = cls()

        # Map each top-level YAML section to its dataclass
        section_map = {f.name: f for f in fields(cls) if not f.name.startswith("_")}
        for section_name, field_info in section_map.items():
            section_data = data.get(section_name)
            if not section_data or not isinstance(section_data, dict):
                continue
            sub_obj = getattr(config, section_name)
            for key, value in section_data.items():
                if hasattr(sub_obj, key):
                    # Coerce to the expected type
                    expected = type(getattr(sub_obj, key))
                    if expected is bool and not isinstance(value, bool):
                        value = str(value).lower() in ("1", "true", "yes")
                    elif expected is int and not isinstance(value, int):
                        value = int(value)
                    elif expected is str and not isinstance(value, str):
                        value = str(value)
                    setattr(sub_obj, key, value)

        config._config_path = path
        return config

    def to_yaml(self, path: str) -> None:
        """Write config to a YAML file."""
        import yaml

        data = {}
        for f in fields(self.__class__):
            if f.name.startswith("_"):
                continue
            sub_obj = getattr(self, f.name)
            section = {}
            for sf in fields(sub_obj.__class__):
                section[sf.name] = getattr(sub_obj, sf.name)
            data[f.name] = section

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_env(cls) -> "Config":
        """Load config from environment variables."""
        config = cls()

        # Proxy
        if host := os.getenv("PROXY_HOST"):
            config.proxy.host = host
        if port := os.getenv("PROXY_PORT"):
            config.proxy.port = int(port)
        if error_pages := os.getenv("ERROR_PAGES_DIR"):
            config.proxy.error_pages_dir = error_pages

        # Wayback
        if date := os.getenv("TARGET_DATE"):
            config.wayback.target_date = date
        if tolerance := os.getenv("DATE_TOLERANCE_DAYS"):
            config.wayback.date_tolerance_days = int(tolerance)

        # Cache
        if redis_url := os.getenv("REDIS_URL"):
            config.cache.redis_url = redis_url
        if ttl := os.getenv("HOT_TTL_SECONDS"):
            config.cache.hot_ttl_seconds = int(ttl)

        # HTTPS
        if ca_cert := os.getenv("CA_CERT_PATH"):
            config.https.ca_cert_path = ca_cert
        if ca_key := os.getenv("CA_KEY_PATH"):
            config.https.ca_key_path = ca_key

        # Access
        if mode := os.getenv("ACCESS_MODE"):
            config.access.mode = mode

        # Throttle
        if speed := os.getenv("THROTTLE_SPEED"):
            config.throttle.default_speed = speed
        if user_override := os.getenv("THROTTLE_USER_OVERRIDE"):
            config.throttle.allow_user_override = user_override.lower() in ("1", "true", "yes")
        if cookie := os.getenv("THROTTLE_COOKIE"):
            config.throttle.cookie_name = cookie

        # Landing page
        if lp_enabled := os.getenv("LANDING_PAGE_ENABLED"):
            config.landing_page.enabled = lp_enabled.lower() not in ("0", "false", "no")
        if lp_dir := os.getenv("LANDING_PAGE_DIR"):
            config.landing_page.template_dir = lp_dir
        if lp_count := os.getenv("LANDING_MOST_VIEWED_COUNT"):
            config.landing_page.most_viewed_count = int(lp_count)

        # Header bar
        if hb_enabled := os.getenv("HEADER_BAR_ENABLED"):
            config.header_bar.enabled = hb_enabled.lower() in ("1", "true", "yes")
        if hb_pos := os.getenv("HEADER_BAR_POSITION"):
            config.header_bar.position = hb_pos
        if hb_text := os.getenv("HEADER_BAR_TEXT"):
            config.header_bar.custom_text = hb_text
        if hb_css := os.getenv("HEADER_BAR_CSS"):
            config.header_bar.custom_css = hb_css

        # Admin
        if admin_enabled := os.getenv("ADMIN_ENABLED"):
            config.admin.enabled = admin_enabled.lower() in ("1", "true", "yes")
        if admin_pw := os.getenv("ADMIN_PASSWORD"):
            config.admin.password = admin_pw

        # Crawler
        if crawl_conc := os.getenv("CRAWL_CONCURRENCY"):
            config.crawler.concurrency = int(crawl_conc)
        if crawl_max := os.getenv("CRAWL_MAX_URLS"):
            config.crawler.max_urls = int(crawl_max)

        return config
