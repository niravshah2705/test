from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_typescript_route_and_api_placeholders_exist() -> None:
    expected = [
        "app/page.tsx",
        "app/search/page.tsx",
        "app/hotels/[hotelId]/page.tsx",
        "app/booking/page.tsx",
        "app/bookings/page.tsx",
        "app/account/page.tsx",
        "app/(admin)/admin/hotels/page.tsx",
        "app/(admin)/admin/rooms/page.tsx",
        "app/(admin)/admin/reservations/page.tsx",
        "app/(admin)/admin/availability-blocks/page.tsx",
        "app/api/search/route.ts",
        "app/api/hotels/route.ts",
        "app/api/availability/route.ts",
        "app/api/reservations/route.ts",
        "app/api/payments/route.ts",
        "app/api/account/route.ts",
        "app/api/admin/route.ts",
    ]
    missing = [path for path in expected if not (ROOT / path).exists()]
    assert missing == []


def test_domain_modules_and_shared_libraries_exist() -> None:
    domains = ["hotel", "room", "availability", "reservation", "payment", "user", "admin", "audit"]
    for domain in domains:
        module = ROOT / "src" / "domain" / domain
        assert module.is_dir()
        for filename in ["index.ts", "service.ts", "repository.ts", "schemas.ts", "dto.ts", "types.ts"]:
            assert (module / filename).exists()

    for library in ["db", "auth", "validation", "errors", "date", "money", "request"]:
        assert (ROOT / "src" / "lib" / library / "index.ts").exists()


def test_import_aliases_and_server_only_database_boundary() -> None:
    tsconfig = (ROOT / "tsconfig.json").read_text()
    assert '"@/domain/*"' in tsconfig
    assert '"@/lib/*"' in tsconfig
    assert '"@/ui/*"' in tsconfig

    db_index = (ROOT / "src" / "lib" / "db" / "index.ts").read_text()
    assert 'import "server-only";' in db_index


def test_hotel_public_exports_hide_repository_details() -> None:
    hotel_index = (ROOT / "src" / "domain" / "hotel" / "index.ts").read_text()
    assert "createHotelService" in hotel_index
    assert "HotelService" in hotel_index
    assert "repository" not in hotel_index.lower()


def test_architecture_readme_documents_boundaries() -> None:
    readme = (ROOT / "docs" / "application-architecture.md").read_text().lower()
    for term in ["routes", "services", "repositories", "schemas", "dtos", "ui components", "server-only"]:
        assert term in readme
