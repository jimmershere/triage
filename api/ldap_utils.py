"""Helpers for integrating TurboEDI authentication with OpenLDAP."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional

try:  # pragma: no cover - optional dependency for production deployments
    from ldap3 import BASE, SUBTREE, Connection, MODIFY_REPLACE, Server
    from ldap3.core.exceptions import LDAPException
except Exception:  # pragma: no cover - the optional dependency is not installed
    BASE = SUBTREE = None
    Connection = Server = None  # type: ignore
    MODIFY_REPLACE = None
    LDAPException = Exception  # type: ignore

LOGGER = logging.getLogger("api.ldap")


def generate_ssha(password: str, salt: Optional[bytes] = None) -> str:
    """Return an SSHA hash for use with OpenLDAP userPassword attributes."""

    if not password:
        raise ValueError("password required for SSHA hash")
    if salt is None:
        salt = os.urandom(4)
    digest = hashlib.sha1(password.encode("utf-8") + salt).digest()
    return "{SSHA}" + base64.b64encode(digest + salt).decode("ascii")


_RDN_ESCAPE = re.compile(r"([,=+<>#;\\\"])")


def escape_rdn(value: str) -> str:
    """Escape a relative distinguished name component."""

    if value is None:
        return ""
    trimmed = value.strip()
    escaped = _RDN_ESCAPE.sub(lambda m: "\\" + m.group(1), trimmed)
    if escaped and (escaped[0] == "#" or escaped[0] == " "):
        escaped = "\\" + escaped
    if escaped and escaped[-1] == " ":
        escaped = escaped[:-1] + "\\ "
    return escaped


@dataclass
class LDAPConfig:
    uri: str
    base_dn: str
    root_cn: str
    users_ou: str
    roles_ou: str
    trading_partners_ou: str
    bind_dn: str
    bind_password: str
    timeout: int
    bootstrap_username: str
    bootstrap_password_hash: str
    bootstrap_password_plain: Optional[str] = None


class LDAPUnavailableError(RuntimeError):
    """Raised when LDAP operations are requested but ldap3 is unavailable."""


class LDAPManager:
    def __init__(
        self,
        config: LDAPConfig,
        role_hierarchy: List[str],
        role_aliases: dict[str, str],
        administrator_role: str,
    ) -> None:
        if Connection is None or Server is None:
            raise LDAPUnavailableError("ldap3 is not installed")
        self.config = config
        self.role_hierarchy = [r.lower() for r in role_hierarchy]
        self.role_aliases = {k.lower(): v.lower() for k, v in role_aliases.items()}
        self.administrator_role = administrator_role.lower()
        if self.administrator_role not in self.role_hierarchy:
            raise ValueError("administrator role must be in hierarchy")
        self.root_dn = f"{config.root_cn},{config.base_dn}" if config.root_cn else config.base_dn
        self.users_dn = f"{config.users_ou},{self.root_dn}" if config.users_ou else self.root_dn
        self.roles_dn = f"{config.roles_ou},{self.root_dn}" if config.roles_ou else self.root_dn
        self.trading_dn = (
            f"{config.trading_partners_ou},{self.root_dn}" if config.trading_partners_ou else self.root_dn
        )
        self.bootstrap_dn = f"uid={escape_rdn(config.bootstrap_username)},{self.users_dn}"

    # ----- helpers -----
    def _server(self) -> Server:
        return Server(self.config.uri, connect_timeout=self.config.timeout or None)

    @contextmanager
    def connection(self, *, user: Optional[str] = None, password: Optional[str] = None) -> Iterator[Connection]:
        conn = Connection(
            self._server(),
            user=user or (self.config.bind_dn or None),
            password=password or (self.config.bind_password or None),
            receive_timeout=self.config.timeout or None,
            auto_bind=True,
        )
        try:
            yield conn
        finally:  # pragma: no cover - network cleanup
            try:
                conn.unbind()
            except Exception:
                pass

    def _normalize_role(self, role: Optional[str]) -> str:
        if not role:
            return self.role_hierarchy[0]
        cleaned = role.strip().lower()
        resolved = self.role_aliases.get(cleaned, cleaned)
        return resolved if resolved in self.role_hierarchy else self.role_hierarchy[0]

    def _role_groups(self, role: str) -> List[str]:
        normalized = self._normalize_role(role)
        groups: List[str] = []
        for name in self.role_hierarchy:
            groups.append(self._role_group_dn(name))
            if name == normalized:
                break
        return groups

    def _role_group_dn(self, role: str) -> str:
        return f"cn={escape_rdn(role)},{self.roles_dn}"

    def _ensure_entry(self, conn: Connection, dn: str, object_classes: List[str], attributes: dict[str, List[str]]) -> None:
        if not dn:
            return
        exists = conn.search(dn, "(objectClass=*)", search_scope=BASE, attributes=["dn"])
        if exists and conn.entries:
            modifications = {}
            for key, value in attributes.items():
                modifications[key] = [(MODIFY_REPLACE, value)]
            if modifications:
                conn.modify(dn, modifications)
            return
        conn.add(dn, object_classes, attributes)

    # ----- bootstrap -----
    def bootstrap(self, trading_partner_ids: Iterable[str]) -> None:
        LOGGER.info("Bootstrapping LDAP structure at %s", self.root_dn)
        password_hash = self.config.bootstrap_password_hash
        if self.config.bootstrap_password_plain:
            try:
                password_hash = generate_ssha(self.config.bootstrap_password_plain)
            except Exception:
                LOGGER.exception("Failed to hash bootstrap administrator password")
        with self.connection() as conn:
            self._ensure_entry(
                conn,
                self.root_dn,
                ["top", "organizationalRole"],
                {"cn": [self.config.root_cn.replace("cn=", "")] if self.config.root_cn else ["HEDI"]},
            )
            self._ensure_entry(conn, self.users_dn, ["top", "organizationalUnit"], {"ou": ["users"]})
            self._ensure_entry(conn, self.roles_dn, ["top", "organizationalUnit"], {"ou": ["roles"]})
            self._ensure_entry(
                conn,
                self.trading_dn,
                ["top", "organizationalUnit"],
                {"ou": ["trading-partners"]},
            )
            self._ensure_admin_user(conn, password_hash)
            self._ensure_role_groups(conn)
            self._ensure_admin_membership(conn)
            self._ensure_trading_partners(conn, trading_partner_ids)

    def _ensure_admin_user(self, conn: Connection, password_hash: str) -> None:
        attributes = {
            "uid": [self.config.bootstrap_username],
            "cn": ["Bootstrap Administrator"],
            "sn": ["Administrator"],
            "givenName": ["Bootstrap"],
            "displayName": ["HEDI Administrator"],
            "userPassword": [password_hash],
        }
        exists = conn.search(self.bootstrap_dn, "(objectClass=*)", search_scope=BASE, attributes=["uid", "userPassword"])
        if exists and conn.entries:
            entry = conn.entries[0]
            current_hash = None
            try:
                current_hash = str(entry.userPassword)
            except Exception:
                pass
            if current_hash != password_hash:
                conn.modify(self.bootstrap_dn, {"userPassword": [(MODIFY_REPLACE, [password_hash])]})
            return
        conn.add(
            self.bootstrap_dn,
            ["top", "person", "organizationalPerson", "inetOrgPerson"],
            attributes,
        )

    def _ensure_role_groups(self, conn: Connection) -> None:
        descriptions = {
            "view": "Read-only portal access",
            "create": "Create/upload permissions",
            "update": "Update and mapping permissions",
            self.administrator_role: "Full administrator access",
        }
        for role in self.role_hierarchy:
            dn = self._role_group_dn(role)
            desc = descriptions.get(role, f"HEDI {role} role")
            attrs = {
                "cn": [role],
                "description": [desc],
                "member": [self.bootstrap_dn],
            }
            self._ensure_entry(conn, dn, ["top", "groupOfNames"], attrs)

    def _ensure_admin_membership(self, conn: Connection) -> None:
        for role in self.role_hierarchy:
            dn = self._role_group_dn(role)
            conn.modify(
                dn,
                {
                    "member": [(MODIFY_REPLACE, list({self.bootstrap_dn} | self._group_members(conn, dn)))],
                },
            )

    def _group_members(self, conn: Connection, dn: str) -> set[str]:
        if not conn.search(dn, "(objectClass=*)", search_scope=BASE, attributes=["member"]):
            return set()
        entry = conn.entries[0]
        try:
            values = entry.member.values
        except Exception:
            return set()
        return {str(v) for v in values}

    def _ensure_trading_partners(self, conn: Connection, partner_ids: Iterable[str]) -> None:
        for partner in partner_ids:
            if not partner:
                continue
            dn = f"ou={escape_rdn(str(partner))},{self.trading_dn}"
            self._ensure_entry(
                conn,
                dn,
                ["top", "organizationalUnit"],
                {"ou": [str(partner)], "description": ["Trading partner access scope"]},
            )

    # ----- public API -----
    def authenticate(self, username: str, password: str) -> Optional[dict]:
        username = (username or "").strip()
        if not username or not password:
            return None
        user_dn = f"uid={escape_rdn(username)},{self.users_dn}"
        try:
            with self.connection(user=user_dn, password=password):
                pass
        except LDAPException:
            return None
        return self.fetch_user(username)

    def fetch_user(self, username: str) -> Optional[dict]:
        username = (username or "").strip()
        if not username:
            return None
        user_dn = f"uid={escape_rdn(username)},{self.users_dn}"
        with self.connection() as conn:
            found = conn.search(
                self.users_dn,
                f"(uid={escape_rdn(username)})",
                search_scope=SUBTREE,
                attributes=["uid", "cn"],
            )
            if not found or not conn.entries:
                return None
            highest_role = self._user_highest_role(conn, user_dn)
            allow_admin = highest_role == self.administrator_role
            allow_portal = highest_role in self.role_hierarchy
            return {
                "username": username,
                "role": highest_role,
                "allow_portal": allow_portal,
                "allow_admin": allow_admin,
                "created_at": None,
                "updated_at": None,
            }

    def _user_highest_role(self, conn: Connection, user_dn: str) -> str:
        memberships: List[str] = []
        for role in self.role_hierarchy:
            group_dn = self._role_group_dn(role)
            if conn.search(group_dn, "(objectClass=*)", search_scope=BASE, attributes=["member"]):
                entry = conn.entries[0]
                try:
                    members = {str(v).lower() for v in entry.member.values}
                except Exception:
                    members = set()
                if user_dn.lower() in members:
                    memberships.append(role)
        if memberships:
            return memberships[-1]
        return self.role_hierarchy[0]

    def list_users(self) -> List[dict]:
        with self.connection() as conn:
            conn.search(
                self.users_dn,
                "(uid=*)",
                search_scope=SUBTREE,
                attributes=["uid"],
            )
            users = []
            for entry in conn.entries:
                username = str(entry.uid)
                profile = self.fetch_user(username)
                if profile:
                    users.append(profile)
            return sorted(users, key=lambda item: item["username"].lower())

    def sync_user(self, username: str, role: str, password: Optional[str], *, portal: bool, admin: bool) -> dict:
        username = (username or "").strip()
        if not username:
            raise ValueError("username required")
        normalized_role = self._normalize_role(role)
        if admin:
            normalized_role = self.administrator_role
        dn = f"uid={escape_rdn(username)},{self.users_dn}"
        password_hash = None
        if password:
            password_hash = generate_ssha(password)
        with self.connection() as conn:
            exists = conn.search(dn, "(objectClass=*)", search_scope=BASE, attributes=["uid"])
            if not exists or not conn.entries:
                attrs = {
                    "uid": [username],
                    "cn": [username],
                    "sn": [username or "User"],
                    "givenName": [username or "User"],
                }
                if password_hash:
                    attrs["userPassword"] = [password_hash]
                conn.add(
                    dn,
                    ["top", "person", "organizationalPerson", "inetOrgPerson"],
                    attrs,
                )
            elif password_hash:
                conn.modify(dn, {"userPassword": [(MODIFY_REPLACE, [password_hash])]})
            desired_groups = set(self._role_groups(normalized_role))
            if admin:
                desired_groups.update(self._role_groups(self.administrator_role))
            for group_role in self.role_hierarchy:
                group_dn = self._role_group_dn(group_role)
                members = self._group_members(conn, group_dn)
                if group_dn in desired_groups:
                    members.add(dn)
                else:
                    members.discard(dn)
                conn.modify(group_dn, {"member": [(MODIFY_REPLACE, sorted(members))]})
        profile = self.fetch_user(username)
        if profile:
            return profile
        return {
            "username": username,
            "role": normalized_role,
            "allow_portal": portal or normalized_role in self.role_hierarchy,
            "allow_admin": admin or normalized_role == self.administrator_role,
            "created_at": None,
            "updated_at": None,
        }

    def delete_user(self, username: str) -> None:
        username = (username or "").strip()
        if not username:
            return
        dn = f"uid={escape_rdn(username)},{self.users_dn}"
        with self.connection() as conn:
            conn.delete(dn)
            for role in self.role_hierarchy:
                group_dn = self._role_group_dn(role)
                members = self._group_members(conn, group_dn)
                if dn in members:
                    members.remove(dn)
                    conn.modify(group_dn, {"member": [(MODIFY_REPLACE, sorted(members))]})
