"""Integration tests for the auth router."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_google_login_creates_user(client, patch_google):
    response = await client.post("/auth/google", json={"id_token": "fake"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["user"]["email"] == "father@example.com"
    assert data["user"]["full_name"] == "Mr. Test Engineer"


@pytest.mark.asyncio
async def test_google_login_is_idempotent(client, patch_google):
    r1 = await client.post("/auth/google", json={"id_token": "fake"})
    r2 = await client.post("/auth/google", json={"id_token": "fake"})
    assert r1.json()["user"]["id"] == r2.json()["user"]["id"]


@pytest.mark.asyncio
async def test_me_requires_token(client):
    r = await client.get("/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_with_token(client, patch_google):
    login = await client.post("/auth/google", json={"id_token": "fake"})
    token = login.json()["access_token"]
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "father@example.com"
