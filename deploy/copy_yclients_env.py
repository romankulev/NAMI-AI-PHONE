from pathlib import Path


SOURCE = Path("/home/roman/n8n/n8n.env")
TARGET = Path("/home/roman/apps/openai-realtime-miniapp/.env")
KEYS = {"YC_PARTNER_TOKEN", "YC_COMPANY_ID", "YC_API_BASE_URL"}


def parse(path: Path) -> list[tuple[str, str]]:
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result.append((key, value))
    return result


source_values = {key: value for key, value in parse(SOURCE) if key in KEYS}
if source_values.keys() != KEYS:
    raise SystemExit("Required YCLIENTS settings are missing")

target_values = parse(TARGET)
merged = [(key, value) for key, value in target_values if key not in KEYS]
merged.extend((key, source_values[key]) for key in sorted(KEYS))
TARGET.write_text(
    "\n".join(f"{key}={value}" for key, value in merged) + "\n",
    encoding="utf-8",
)
TARGET.chmod(0o600)
