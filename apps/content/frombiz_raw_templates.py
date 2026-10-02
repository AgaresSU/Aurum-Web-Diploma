import json
import re
import shutil
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.utils._os import safe_join

RAW_FROMBIZ_SLUGS = {
    "business-technology-conference",
    "analytics-startup-platform",
    "apartment-renovation-landing",
    "app-healthy-nutrition",
    "auto-service-landing",
    "beauty-salon-landing",
    "branding-agency-showcase",
    "business-app-landing",
    "business-masterclass-event",
    "cargo-freight-landing",
    "cleaning-service-company",
    "coffee-house-landing",
    "creative-web-studio",
    "event-agency-portfolio",
    "fashion-atelier-studio",
    "food-delivery-market",
    "game-app-development",
    "home-design-store",
    "interior-architecture-studio",
    "it-company-corporate",
    "it-outstaffing-team",
    "legal-consulting-services",
    "online-language-school",
    "pipe-manufacturing-corporate",
    "residential-complex-breeze",
    "salon-equipment-catalog",
    "taxi-fleet-landing",
}

RAW_TEMPLATE_CLEANUP_VERSION = "20260923-mobile-preview-fit"
RAW_TEMPLATE_ASSET_FALLBACK = "/assets/brand/aurumweb-favicon-192.png"
SHARED_ASSET_DIR_NAME = "_shared"
SHARED_ASSET_MANIFEST_NAME = "manifest.json"

DEVICE_ICONS = {
    "pc": (
        '<svg class="aurum-device-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none">'
        '<rect x="4" y="5" width="16" height="11" rx="1.8" stroke="currentColor" stroke-width="2"/>'
        '<path d="M9 20h6M12 16v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
        "</svg>"
    ),
    "tablet": (
        '<svg class="aurum-device-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none">'
        '<rect x="7" y="3" width="10" height="18" rx="2.2" stroke="currentColor" stroke-width="2"/>'
        '<path d="M11.5 18h1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
        "</svg>"
    ),
    "mobile": (
        '<svg class="aurum-device-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none">'
        '<rect x="8" y="3" width="8" height="18" rx="2" stroke="currentColor" stroke-width="2"/>'
        '<path d="M11.5 18h1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
        "</svg>"
    ),
}

RAW_TEMPLATE_CLEANUP_CSS = """
/* Device preview controls */
.aurum-device-toolbar {
  left: 50% !important;
  right: auto !important;
  bottom: 18px !important;
  width: auto !important;
  max-width: calc(100vw - 32px);
  transform: translateX(-50%);
  padding: 10px 12px !important;
  gap: 14px;
  border: 1px solid rgba(255, 255, 255, 0.18) !important;
  border-radius: 999px;
  background: rgba(9, 22, 15, 0.92) !important;
  box-shadow: 0 20px 52px rgba(0, 0, 0, 0.36);
  backdrop-filter: blur(18px);
}

.aurum-device-toolbar::before {
  content: "Посмотреть как на устройстве";
  color: rgba(255, 248, 230, 0.78);
  font-size: 13px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
}

.aurum-device-toolbar > div {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.aurum-device-toolbar .aurum-device-icon {
  display: block;
  width: 17px;
  height: 17px;
}

.aurum-device-toolbar .aurum-device-text {
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
}

.aurum-device-toolbar label[for="pc_screen"],
.aurum-device-toolbar label[for="tablet_screen"],
.aurum-device-toolbar label[for="mobile_screen"] {
  display: inline-flex !important;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-width: 0;
  min-height: 42px;
  margin: 0 !important;
  padding: 0 16px !important;
  border: 1px solid rgba(255, 248, 230, 0.24) !important;
  border-radius: 999px !important;
  color: rgba(255, 248, 230, 0.86) !important;
  background: rgba(255, 255, 255, 0.06) !important;
  box-shadow: none !important;
}

.aurum-device-toolbar #pc_screen:checked + label[for="pc_screen"],
.aurum-device-toolbar #tablet_screen:checked + label[for="tablet_screen"],
.aurum-device-toolbar #mobile_screen:checked + label[for="mobile_screen"] {
  color: #07110b !important;
  border-color: #e5bd5c !important;
  background: linear-gradient(135deg, #f8df8f 0%, #c9972e 100%) !important;
}

html.aurum-embedded-device-preview .aurum-device-toolbar {
  display: none !important;
}

@media (max-width: 720px) {
  #preview-container {
    padding-left: 8px !important;
    padding-right: 8px !important;
  }

  #preview-container-mobile {
    width: min(418px, 100%);
  }

  #preview-container-mobile > div {
    width: 100%;
    padding-left: 12px !important;
    padding-right: 12px !important;
  }

  #preview-container-mobile iframe {
    width: 100% !important;
  }

  .aurum-device-toolbar {
    bottom: 10px !important;
    flex-wrap: wrap;
    justify-content: center !important;
    border-radius: 22px;
  }

  .aurum-device-toolbar::before {
    width: 100%;
    text-align: center;
  }

  .aurum-device-toolbar label[for="pc_screen"],
  .aurum-device-toolbar label[for="tablet_screen"],
  .aurum-device-toolbar label[for="mobile_screen"] {
    min-height: 38px;
    padding: 0 12px !important;
  }

  .aurum-device-toolbar .aurum-device-text {
    font-size: 12px;
  }
}
""".strip()

DEVICE_PREVIEW_MODE_SCRIPT = """
<script>
  (function () {
    try {
      if (new URLSearchParams(window.location.search).has("no_footer_label")) {
        document.documentElement.classList.add("aurum-embedded-device-preview");
      }
    } catch (error) {}
  })();
</script>
""".strip()


def raw_frombiz_source_dir(slug):
    return settings.WEBSITE_DIR / "source_templates" / "frombiz" / slug


def raw_frombiz_shared_dir():
    return settings.WEBSITE_DIR / "source_templates" / "frombiz" / SHARED_ASSET_DIR_NAME


@lru_cache(maxsize=1)
def shared_asset_manifest():
    manifest_path = raw_frombiz_shared_dir() / SHARED_ASSET_MANIFEST_NAME
    if not manifest_path.is_file():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def has_raw_frombiz_template(slug):
    return slug in RAW_FROMBIZ_SLUGS and (raw_frombiz_source_dir(slug) / "index.html").is_file()


def read_raw_frombiz_index(slug):
    index_path = raw_frombiz_source_dir(slug) / "index.html"
    html = index_path.read_text(encoding="utf-8")
    html = _cleanup_raw_template_html(html, slug=slug)
    return re.sub(
        r"assets/aurum-palette\.css\?v=[^\"']+",
        f"assets/aurum-palette.css?v={RAW_TEMPLATE_CLEANUP_VERSION}",
        html,
    )


def read_raw_frombiz_palette_css(slug):
    palette_path = raw_frombiz_asset_path(slug, "aurum-palette.css")
    if palette_path is None:
        raise FileNotFoundError(f"Palette stylesheet is missing for template {slug}.")
    css = palette_path.read_text(encoding="utf-8")
    if "Device preview controls" in css:
        return css
    return f"{css.rstrip()}\n\n{RAW_TEMPLATE_CLEANUP_CSS}\n"


def copy_raw_frombiz_template(template, output_dir):
    source_dir = raw_frombiz_source_dir(template.slug)
    target_dir = Path(output_dir) / template.slug
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir)
    _materialize_shared_assets(template.slug, target_dir)
    _apply_raw_template_cleanup(target_dir)


def raw_frombiz_asset_path(slug, path):
    normalized_path = str(path).replace("\\", "/").lstrip("/")
    local_path = Path(safe_join(raw_frombiz_source_dir(slug) / "assets", normalized_path))
    if local_path.is_file():
        return local_path

    shared_name = shared_asset_manifest().get(slug, {}).get(normalized_path)
    if not shared_name:
        return None
    shared_path = Path(safe_join(raw_frombiz_shared_dir(), shared_name))
    return shared_path if shared_path.is_file() else None


def _materialize_shared_assets(slug, target_dir):
    for relative_path, shared_name in shared_asset_manifest().get(slug, {}).items():
        source_path = Path(safe_join(raw_frombiz_shared_dir(), shared_name))
        target_path = Path(safe_join(target_dir / "assets", relative_path))
        if not source_path.is_file():
            raise FileNotFoundError(f"Shared template asset is missing: {shared_name}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)


def _apply_raw_template_cleanup(target_dir):
    index_path = target_dir / "index.html"
    if index_path.is_file():
        html = index_path.read_text(encoding="utf-8")
        html = _cleanup_raw_template_html(html, slug=target_dir.name)
        html = re.sub(
            r"assets/aurum-palette\.css\?v=[^\"']+",
            f"assets/aurum-palette.css?v={RAW_TEMPLATE_CLEANUP_VERSION}",
            html,
        )
        index_path.write_text(html, encoding="utf-8")

    palette_path = target_dir / "assets" / "aurum-palette.css"
    if not palette_path.is_file():
        return

    css = palette_path.read_text(encoding="utf-8")
    if "Device preview controls" in css:
        return
    palette_path.write_text(f"{css.rstrip()}\n\n{RAW_TEMPLATE_CLEANUP_CSS}\n", encoding="utf-8")


def _normalized_asset_stem(value):
    value = value.casefold().removeprefix("thumb_")
    return re.sub(r"[^a-zа-яё0-9]+", "", value)


def _local_asset_url(slug, remote_url):
    parsed = urlsplit(remote_url)
    mirror_dir = raw_frombiz_source_dir(slug) / "assets" / "mirror" / parsed.netloc
    if not mirror_dir.is_dir():
        return RAW_TEMPLATE_ASSET_FALLBACK

    remote_stem = _normalized_asset_stem(Path(unquote(parsed.path)).stem)
    if not remote_stem:
        return RAW_TEMPLATE_ASSET_FALLBACK

    candidates = []
    for candidate in mirror_dir.iterdir():
        if not candidate.is_file():
            continue
        candidate_stem = _normalized_asset_stem(candidate.stem)
        if candidate_stem.startswith(remote_stem) or remote_stem.startswith(candidate_stem):
            candidates.append(candidate)
    if not candidates:
        return RAW_TEMPLATE_ASSET_FALLBACK

    selected = sorted(candidates, key=lambda item: (len(item.name), item.name.casefold()))[0]
    return f"assets/mirror/{parsed.netloc}/{selected.name}"


def _localize_remote_template_assets(html, slug):
    resource_pattern = re.compile(
        r'(?P<prefix>\b(?:src|data-original)=["\'])(?P<url>https://[^"\']+\.tpl\.from\.biz/[^"\']+)(?P<suffix>["\'])',
        flags=re.I,
    )
    html = resource_pattern.sub(
        lambda match: (f"{match.group('prefix')}{_local_asset_url(slug, match.group('url'))}{match.group('suffix')}"),
        html,
    )

    css_url_pattern = re.compile(
        r"url\((?P<quote>['\"]?)(?P<url>https://[^'\")]+\.tpl\.from\.biz/[^'\"]+)(?P=quote)\)",
        flags=re.I,
    )
    return css_url_pattern.sub(
        lambda match: f"url('{_local_asset_url(slug, match.group('url'))}')",
        html,
    )


def _cleanup_raw_template_html(html, slug=""):
    html = re.sub(r'\s*<link rel="preconnect" href="https://mc\.yandex\.ru">\s*', "\n", html, flags=re.I)
    html = re.sub(
        r'\s*<link\b[^>]*href=["\']https://fonts\.(?:googleapis|gstatic)\.com[^>]*>\s*',
        "\n",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"\s*<!--\s*Yandex\.Metrika counter\s*-->.*?<!--\s*/Yandex\.Metrika counter\s*-->",
        "\n",
        html,
        flags=re.S | re.I,
    )
    html = re.sub(r'\s*<link rel="preconnect" href="https://from\.biz">\s*', "\n", html, flags=re.I)
    html = re.sub(r'\s*<meta name="generator" content="ru\.from\.biz">\s*', "\n", html, flags=re.I)
    html = re.sub(
        r'\s*<meta property="og:url" content="https://[^"]*\.tpl\.from\.biz/?">\s*',
        "\n",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'\s*<link rel="canonical" href="https://[^"]*\.tpl\.from\.biz/?">\s*',
        "\n",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"Демонстрационный\s+сайт-шаблон\s+конструктора\s+Фром",
        "Демонстрационный пример сайта",
        html,
    )
    html = re.sub(r"test@from\.biz", "info@example.ru", html, flags=re.I)
    html = re.sub(r"@test\.from\.biz", "@company_demo", html, flags=re.I)
    html = re.sub(
        r'(?P<prefix>\bhref=["\'])https://(?:vk\.com|ok\.ru|t\.me|wa\.me|www\.apple\.com|play\.google\.com)/?[^"\']*(?P<suffix>["\'])',
        lambda match: f"{match.group('prefix')}/brief/?template={slug}{match.group('suffix')}",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(?P<prefix>\bhref=["\'])https://[^"\']+\.tpl\.from\.biz/?[^"\']*(?P<suffix>["\'])',
        lambda match: f"{match.group('prefix')}/brief/?template={slug}{match.group('suffix')}",
        html,
        flags=re.I,
    )
    if slug:
        html = _localize_remote_template_assets(html, slug)
    if "aurum-embedded-device-preview" not in html:
        html = html.replace("</head>", f"{DEVICE_PREVIEW_MODE_SCRIPT}\n</head>", 1)
    html = re.sub(
        r'(<div\b[^>]*class=")([^"]*\bfixed-bottom\b[^"]*)"',
        lambda match: (
            match.group(0)
            if "aurum-device-toolbar" in match.group(2)
            else f'{match.group(1)}{match.group(2)} aurum-device-toolbar"'
        ),
        html,
    )
    html = re.sub(
        r"\s*<a\b[^>]*>\s*(?:<i\b[^>]*></i>\s*)?(?:\+\s*)?Создать сайт\s*</a>",
        "",
        html,
        flags=re.S | re.I,
    )
    html = re.sub(
        r"\s*<button\b[^>]*>\s*(?:<i\b[^>]*></i>\s*)?(?:\+\s*)?Создать сайт\s*</button>",
        "",
        html,
        flags=re.S | re.I,
    )
    for control_id, icon, label in (
        ("pc_screen", DEVICE_ICONS["pc"], "Компьютер"),
        ("tablet_screen", DEVICE_ICONS["tablet"], "Планшет"),
        ("mobile_screen", DEVICE_ICONS["mobile"], "Телефон"),
    ):
        html = re.sub(
            rf'(<label\b[^>]*for="{control_id}"[^>]*>).*?(</label>)',
            lambda match, icon=icon, label=label: (
                re.sub(r'\s+data-bs-(?:toggle|title)="[^"]*"', "", match.group(1))
                + icon
                + f'<span class="aurum-device-text">{label}</span>'
                + match.group(2)
            ),
            html,
            flags=re.S,
        )
    return html
