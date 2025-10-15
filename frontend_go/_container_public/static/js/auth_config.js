window.HEDI_AUTHZ = window.HEDI_AUTHZ || {
  currentUser: null,
  defaultRole: "view",
  providers: {
    database: { enabled: true, notes: "Accounts stored in PostgreSQL (app_users)" },
    ldap: { enabled: false, url: "ldap://ldap.example.com", baseDN: "dc=example,dc=com" },
    oss: { enabled: false, name: "OIDC", issuer: "https://sso.example.com/realms/hedi" },
  },
};
