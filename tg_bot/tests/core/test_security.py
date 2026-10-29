import pytest

from app.core.security import ProjectPasswordManager


async def test_password_roundtrip_and_public_id():
    pm = ProjectPasswordManager()
    public_id = pm.new_public_id()
    password = pm.generate(public_id)
    assert len(password) <= 200  # помещается во всплывающее окно Telegram
    assert pm.extract_public_id(f"  {password}\n") == public_id
    password_hash = await pm.hash(password)
    assert password not in password_hash
    assert await pm.verify(password_hash, password) is True
    assert await pm.verify(password_hash, password + "x") is False
    assert await pm.verify("garbage", password) is False


@pytest.mark.parametrize(
    "value", ["nohyphen", "ZZZZZZZZ-abc", "a1b2c3d4-", "a1b2c3d-abc", "a1b2c3d4e-abc", "", "   "]
)
def test_extract_public_id_rejects_malformed(value):
    assert ProjectPasswordManager.extract_public_id(value) is None


def test_generated_passwords_are_unique():
    passwords = {ProjectPasswordManager.generate("a1b2c3d4") for _ in range(100)}
    assert len(passwords) == 100
