import os
import json
import requests
import tempfile
import zipfile
import shutil
from pathlib import Path
from urllib.parse import quote, urlparse
from decimal import Decimal

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient
from access402_fastapi import Access402

os.environ.setdefault("ACCESS402_MODE", "live")

app = FastAPI(title="ScientificResearchAI")
access402 = Access402.from_env()

with open(Path(__file__).with_name("agent_catalog.json")) as f:
    catalog = json.load(f)

private_dir = Path(tempfile.mkdtemp(
    prefix="private_products_"
))
os.chmod(private_dir, 0o700)

headers = {
    "Authorization": "Bearer " +
    os.environ["GUMROAD_API_KEY"]
}

public_catalog = []

def create_package(product, slug):

    r = requests.get(
        "https://api.gumroad.com/v2/products/" +
        quote(product["id"], safe=""),
        headers=headers,
        timeout=30
    )
    r.raise_for_status()

    details = r.json()["product"]

    if not details.get("published"):
        raise RuntimeError("Urun yayinda degil.")

    files = details.get("files", [])

    if not files:
        raise RuntimeError("Dosya bulunamadi.")

    package = private_dir / (slug + ".zip")

    with zipfile.ZipFile(
        package, "w", zipfile.ZIP_DEFLATED
    ) as z:

        for i, file in enumerate(files, 1):

            link = file["url"]
            parsed = urlparse(link)

            if (
                parsed.scheme != "https"
                or parsed.hostname != "files.gumroad.com"
                or file.get("stream_only")
            ):
                raise RuntimeError("Dosya uygun degil.")

            try:
                with requests.get(
                    link,
                    stream=True,
                    timeout=120
                ) as response:

                    response.raise_for_status()

                    if urlparse(response.url).scheme != "https":
                        raise RuntimeError("HTTPS gerekli.")

                    kind = response.headers.get(
                        "Content-Type", ""
                    ).lower()

                    if any(x in kind for x in [
                        "text/html",
                        "application/xml",
                        "application/json"
                    ]):
                        raise RuntimeError("Dosya yaniti gecersiz.")

                    extension = {
                        "labgraph": ".apk",
                        "quantum-gravity": ".docx",
                        "last-signal": ".zip"
                    }[slug]

                    name = Path(
                        file.get("name") or "file"
                    ).name

                    if not name.endswith(extension):
                        name += extension

                    total = 0

                    with z.open(
                        str(i) + "_" + name, "w"
                    ) as output:

                        for chunk in response.iter_content(65536):
                            output.write(chunk)
                            total += len(chunk)

                    if total == 0:
                        raise RuntimeError("Bos dosya.")

            except Exception:
                raise RuntimeError(
                    "Dosya aktarimi basarisiz."
                ) from None

    with zipfile.ZipFile(package) as z:
        if z.testzip() is not None:
            raise RuntimeError("ZIP butunluk hatasi.")

    return package


def register_product(product, slug, package):

    price = str(
        Decimal(str(product["price_minor_units"]))
        / Decimal(100)
    )

    route = "/buy/" + slug

    public_url = os.environ[
        "ACCESS402_PUBLIC_BASE_URL"
    ]

    @app.get(
        route,
        name="buy_" + slug,
        response_class=FileResponse
    )
    @access402.protect(
        price=price,
        access_type="per_request",
        description=product["name"],
        discovery_enabled=public_url.startswith(
            "https://"
        )
    )
    async def deliver():

        return FileResponse(
            path=package,
            media_type="application/zip",
            filename=slug + ".zip",
            headers={
                "Cache-Control": "private, no-store"
            }
        )

    public_catalog.append({
        "name": product["name"],
        "price_usdc": price,
        "endpoint": route,
        "network": "eip155:8453",
        "payment": "x402",
        "gumroad_url": product.get("gumroad_url")
    })


for p in catalog:

    name = p["name"].lower()

    if "exclusive" in name:
        print("MUNHASIR URUN: Gumroad kanalinda kaldi.")
        continue

    if name.startswith("labgraph"):
        slug = "labgraph"
    elif name.startswith("understanding quantum"):
        slug = "quantum-gravity"
    elif name.startswith("last signal"):
        slug = "last-signal"
    else:
        continue

    if p.get("currency", "").lower() != "usd":
        continue

    package = create_package(p, slug)
    register_product(p, slug, package)

    print("TESLIMAT PAKETI HAZIR:", slug)


@app.get("/")
async def home():
    return {
        "seller": "ScientificResearchAI",
        "protocol": "x402 V2",
        "products": public_catalog
    }


access402.install(app)


