from app.services.supabase import SupabaseStore


def test_supabase_disabled_falls_back_to_local_data():
    store = SupabaseStore()
    default = {"wallets": {"wallet-1": {"added_at": "now"}}}

    assert store.enabled is False
    assert store.load("auth_whitelist", default) == default
    assert store.save("auth_whitelist", default) is False


def test_supabase_requires_url_and_service_role_key():
    assert SupabaseStore("https://example.supabase.co", "").enabled is False
    assert SupabaseStore("", "service-role").enabled is False
    assert SupabaseStore("https://example.supabase.co", "service-role").enabled is True
