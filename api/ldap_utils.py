"""Helpers for integrating TurboEDI authentication with OpenLDAP."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Tuple

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


def _parse_component(component: str, default_attr: str = "ou") -> Tuple[str, str]:
    """Return the attribute and value for a DN component.

    Parameters
    ----------
    component:
        A component such as ``"ou=users"`` or simply ``"users"``.
    default_attr:
        Attribute name to assume when none is provided.
    """

    raw = (component or "").strip()
    if not raw:
        return "", ""
    if "=" in raw:
        attr, value = raw.split("=", 1)
    else:
        attr, value = default_attr, raw
    return attr.strip(), value.strip()


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
    admin_dn: Optional[str] = None
    admin_username: Optional[str] = None


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
        self.base_dn = (config.base_dn or "").strip()
        self.root_attribute, self.root_value, self.root_dn = self._normalize_container(
            config.root_cn or "ou=HEDI",
            parent=self.base_dn,
            fallback_value="HEDI",
            default_attr="ou",
        )
        self.users_attribute, self.users_value, self.users_dn = self._normalize_container(
            config.users_ou or "ou=users",
            parent=self.root_dn,
            fallback_value="users",
            default_attr="ou",
        )
        self.roles_attribute, self.roles_value, self.roles_dn = self._normalize_container(
            config.roles_ou or "ou=roles",
            parent=self.root_dn,
            fallback_value="roles",
            default_attr="ou",
        )
        self.trading_attribute, self.trading_value, self.trading_dn = self._normalize_container(
            config.trading_partners_ou or "ou=trading-partners",
            parent=self.root_dn,
            fallback_value="trading-partners",
            default_attr="ou",
        )
        bootstrap_username = (config.bootstrap_username or "").strip() or "admin"
        self.bootstrap_username = bootstrap_username
        self.bootstrap_dn = f"uid={escape_rdn(bootstrap_username)},{self.users_dn}"
        admin_dn = (config.admin_dn or "").strip()
        self.admin_dn = admin_dn or None
        self._admin_dn_lower = admin_dn.lower() if admin_dn else None
        admin_username = (config.admin_username or "").strip()
        self.admin_username = admin_username or None
        self._admin_username_lower = admin_username.lower() if admin_username else None

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

    def _normalize_container(
        self,
        component: str,
        *,
        parent: Optional[str],
        fallback_value: str,
        default_attr: str = "ou",
    ) -> Tuple[str, str, str]:
        attr, value = _parse_component(component, default_attr=default_attr)
        if not value:
            value = fallback_value
        if not attr:
            attr = default_attr
        if attr.lower() != default_attr.lower():
            LOGGER.warning(
                "Normalizing LDAP component %s to %s=%s for hierarchical compatibility",
                component or "<missing>",
                default_attr,
                value,
            )
            attr = default_attr
        normalized = f"{attr}={escape_rdn(value)}"
        parent_dn = (parent or "").strip()
        dn = f"{normalized},{parent_dn}" if parent_dn else normalized
        return attr, value, dn

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

    def _is_admin_username(self, value: str) -> bool:
        if not value or not self._admin_username_lower:
            return False
        return value.strip().lower() == self._admin_username_lower

    def _is_admin_dn(self, value: str) -> bool:
        if not value or not self._admin_dn_lower:
            return False
        return value.strip().lower() == self._admin_dn_lower

    def _admin_profile(self, requested_username: Optional[str] = None) -> dict:
        username = self.admin_username or (requested_username or "admin")
        return {
            "username": username,
            "role": self.administrator_role,
            "allow_portal": True,
            "allow_admin": True,
            "created_at": None,
            "updated_at": None,
        }

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
                ["top", "organizationalUnit"],
                {self.root_attribute: [self.root_value]},
            )
            self._ensure_entry(
                conn,
                self.users_dn,
                ["top", "organizationalUnit"],
                {self.users_attribute: [self.users_value]},
            )
            self._ensure_entry(
                conn,
                self.roles_dn,
                ["top", "organizationalUnit"],
                {self.roles_attribute: [self.roles_value]},
            )
            self._ensure_entry(
                conn,
                self.trading_dn,
                ["top", "organizationalUnit"],
                {self.trading_attribute: [self.trading_value]},
            )
            self._ensure_admin_user(conn, password_hash)
            self._ensure_role_groups(conn)
            self._ensure_admin_membership(conn)
            self._ensure_trading_partners(conn, trading_partner_ids)

    def _ensure_admin_user(self, conn: Connection, password_hash: str) -> None:
        attributes = {
            "uid": [self.bootstrap_username],
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
            "submit": "Submission and update permissions",
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
        if self.admin_dn and (self._is_admin_username(username) or self._is_admin_dn(username)):
            try:
                with self.connection(user=self.admin_dn, password=password):
                    pass
            except LDAPException:
                return None
            return self._admin_profile(username)
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
        if self.admin_dn and (self._is_admin_username(username) or self._is_admin_dn(username)):
            canonical = self.admin_username or username
            try:
                with self.connection() as conn:
                    found = conn.search(
                        self.admin_dn,
                        "(objectClass=*)",
                        search_scope=BASE,
                        attributes=["cn", "uid"],
                    )
                    if found and conn.entries:
                        return self._admin_profile(canonical)
            except LDAPException:
                LOGGER.debug("Failed to inspect admin DN %s", self.admin_dn)
            return self._admin_profile(canonical)
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
                try:
                    conn.modify(group_dn, {"member": [(MODIFY_REPLACE, sorted(members))]})
                except LDAPException:
                    LOGGER.warning("Unable to update LDAP role group %s", group_dn)
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
