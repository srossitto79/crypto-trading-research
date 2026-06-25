from fastapi import APIRouter, Depends
from axiom.api_security import require_operator_access
from axiom import api_core as core

router = APIRouter(tags=["auth"], dependencies=[Depends(require_operator_access)])

@router.post("/api/auth/providers/{provider}/oauth/start")
def start_auth_provider_oauth(provider: str):

    return core.start_auth_provider_oauth(provider)

@router.post("/api/auth/providers/{provider}/oauth/complete")
def complete_auth_provider_oauth(provider: str, body: core.AuthProviderOAuthCompleteBody):
    return core.complete_auth_provider_oauth(provider, body)

@router.get("/api/auth/providers/{provider}/oauth/status")
def get_auth_provider_oauth_status(provider: str, state: str):
    return core.get_auth_provider_oauth_status(provider, state)

@router.post("/api/auth/providers/{provider}/oauth/cancel")
def cancel_auth_provider_oauth(provider: str, state: str):
    return core.cancel_auth_provider_oauth(provider, state)

@router.post("/api/auth/providers/{provider}")
def upsert_auth_provider(provider: str, body: core.AuthProviderProfileBody):
    return core.upsert_auth_provider(provider, body)

@router.delete("/api/auth/providers/{provider}")
def delete_auth_provider(provider: str):

    return core.delete_auth_provider(provider)

@router.post("/api/auth/providers/{provider}/test")
def test_auth_provider(provider: str):

    return core.test_auth_provider(provider)

@router.get("/api/auth/providers")
def get_auth_providers():

    return core.get_auth_providers()

@router.get("/api/settings/api-keys")
def get_settings_api_keys():

    return core.get_settings_api_keys()

@router.post("/api/settings/api-keys")
def post_settings_api_key(body: core.SettingsApiKeyBody):
    return core.post_settings_api_key(body)

@router.delete("/api/settings/api-keys/{source}")
def delete_settings_api_key(source: str):

    return core.delete_settings_api_key(source)

@router.post("/api/settings/api-keys/{source}/test")
def test_settings_api_key(source: str):

    return core.test_settings_api_key(source)
