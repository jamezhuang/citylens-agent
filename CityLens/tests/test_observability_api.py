import asyncio
import httpx
from citylens.api import app, require_user


def test_observability_status_requires_authentication():
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/observability')
            assert response.status_code == 401
    asyncio.run(run())


def test_authenticated_status_contains_no_credentials():
    async def run():
        previous = app.dependency_overrides.get(require_user)
        app.dependency_overrides[require_user] = lambda: {'id': 7}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/api/observability')
                assert response.status_code == 200
                body = response.json()
                assert body['content_capture'] is False
                assert body['cloud_delivery_verified'] is None
                assert not any(k in body for k in ['secret_key', 'public_key', 'api_key', 'authorization'])
        finally:
            if previous is None:
                app.dependency_overrides.pop(require_user, None)
            else:
                app.dependency_overrides[require_user] = previous
    asyncio.run(run())
