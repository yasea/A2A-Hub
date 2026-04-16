import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.mqtt_auth import mosquitto_password_hash, tenant_mqtt_password, tenant_mqtt_username
from app.services.mosquitto_auth_sync import MosquittoAuthSyncService


class MqttAuthTest(unittest.TestCase):
    def test_tenant_credentials_are_stable_per_tenant(self):
        username = tenant_mqtt_username("owner_abc")
        password = tenant_mqtt_password("owner_abc", secret="secret-1")

        self.assertEqual(username, "owner_abc")
        self.assertEqual(password, tenant_mqtt_password("owner_abc", secret="secret-1"))
        self.assertNotEqual(password, tenant_mqtt_password("owner_other", secret="secret-1"))

    def test_mosquitto_password_hash_uses_expected_format(self):
        value = mosquitto_password_hash("password-1", salt=b"123456789012", iterations=1000)

        self.assertTrue(value.startswith("$7$1000$"))
        self.assertEqual(len(value.split("$")), 5)

    def test_mosquitto_auth_sync_writes_password_acl_and_reload_stamp(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            passwordfile = root / "passwordfile"
            aclfile = root / "aclfile"
            stamp = root / "reload.stamp"
            service = MosquittoAuthSyncService(
                passwordfile=str(passwordfile),
                aclfile=str(aclfile),
                reload_stamp=str(stamp),
                topic_base="tenant-bus",
            )

            service.write_files(["owner_a", "owner_b"])

            self.assertIn("owner_a:$7$", passwordfile.read_text(encoding="utf-8"))
            self.assertIn("owner_b:$7$", passwordfile.read_text(encoding="utf-8"))
            self.assertIn("pattern readwrite tenant-bus/%u/#", aclfile.read_text(encoding="utf-8"))
            self.assertTrue(stamp.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
