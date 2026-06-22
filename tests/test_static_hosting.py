from pathlib import Path

import pytest
from fastapi import HTTPException

from app import main


@pytest.mark.asyncio
async def test_root_returns_index_when_static_build_exists(tmp_path, monkeypatch):
    index_path = tmp_path / "index.html"
    index_path.write_text("<html>oncall</html>", encoding="utf-8")
    monkeypatch.setattr(main, "static_dir", tmp_path)

    response = await main.root()

    assert Path(getattr(response, "path", "")) == index_path


@pytest.mark.asyncio
async def test_root_returns_api_welcome_without_static_build(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "static_dir", tmp_path)

    response = await main.root()

    assert response["docs"] == "/docs"
    assert "Welcome" in response["message"]


@pytest.mark.asyncio
async def test_spa_fallback_returns_index_for_frontend_route(tmp_path, monkeypatch):
    index_path = tmp_path / "index.html"
    index_path.write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setattr(main, "static_dir", tmp_path)

    response = await main.spa_fallback("incidents/123")

    assert Path(getattr(response, "path", "")) == index_path


@pytest.mark.asyncio
async def test_spa_fallback_keeps_api_paths_reserved(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    monkeypatch.setattr(main, "static_dir", tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await main.spa_fallback("api/chat")

    assert exc_info.value.status_code == 404
