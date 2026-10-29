from app.core.config import Settings


def test_postgres_dsn_escapes_reserved_characters(monkeypatch) -> None:
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    settings = Settings.model_validate(
        {
            "postgres_host": "127.0.0.1",
            "postgres_port": 5432,
            "postgres_db": "ragdb",
            "postgres_user": "postgres",
            "postgres_password": "rABYgaKFO@:/",
        }
    )

    assert settings.postgres_dsn == "postgresql://postgres:rABYgaKFO%40%3A%2F@127.0.0.1:5432/ragdb"


def test_insecure_settings_are_reported(monkeypatch) -> None:
    for name in ("AUTH_JWT_SECRET", "AUTH_BOOTSTRAP_ADMIN_PASSWORD", "AUTH_ENABLED", "CHAT_DEBUG_ENABLED"):
        monkeypatch.delenv(name, raising=False)

    defaults = Settings().insecure_setting_warnings()
    assert any("AUTH_JWT_SECRET" in warning for warning in defaults)
    assert any("AUTH_BOOTSTRAP_ADMIN_PASSWORD" in warning for warning in defaults)
    assert not any("CHAT_DEBUG_ENABLED" in warning for warning in defaults)

    hardened = Settings(auth_jwt_secret="x" * 40, auth_bootstrap_admin_password="a-real-password")
    assert hardened.insecure_setting_warnings() == []
