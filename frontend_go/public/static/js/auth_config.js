window.HEDI_AUTHZ = window.HEDI_AUTHZ || {
  currentUser: null,
  defaultRole: "view",
  providers: {
    htpasswd: { enabled: true, notes: "Managed via mounted htpasswd files" },
    ldap: { enabled: false, url: "ldap://ldap.example.com", baseDN: "dc=example,dc=com" },
    oss: { enabled: false, name: "OIDC", issuer: "https://sso.example.com/realms/hedi" },
  },
};
