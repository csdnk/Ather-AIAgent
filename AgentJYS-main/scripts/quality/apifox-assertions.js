// Shared executable assertions embedded in the Postman v2.1 / Apifox import.
// Error messages intentionally exclude response bodies and credentials.
function qualityRequire(condition, message) {
  if (!condition) throw new Error(message);
}
function qualityLogin(status, body, userId) {
  qualityRequire(status === 200 && body && body.code === 0, 'Login failed');
  qualityRequire(body.data && String(body.data.userId) === String(userId), 'Unexpected login identity');
  qualityRequire(typeof body.data.accessToken === 'string' && body.data.accessToken.length > 0, 'Missing login token');
}
function qualityIdentity(status, body, userId, tenantId) {
  qualityRequire(status === 200 && body && body.code === 0, 'Identity request failed');
  const d = body.data;
  qualityRequire(d && String(d.user_id) === String(userId), 'Unexpected user');
  qualityRequire(String(d.tenant_id) === String(tenantId), 'Unexpected tenant');
  qualityRequire(d.user_enabled === true && d.tenant_enabled === true, 'Identity disabled');
  qualityRequire(Array.isArray(d.role_codes) && d.role_codes.includes('aether_user'), 'Expected business role');
  qualityRequire(!d.role_codes.includes('aether_platform_admin'), 'Business user gained platform role');
}
function qualityDenied(status, body, code) {
  qualityRequire([200, 400, 401, 403].includes(status), 'Denial must not be a transport or server error');
  qualityRequire(body && body.code === code, 'Expected precise denial code');
  qualityRequire(!body.data || !body.data.accessToken, 'Denied request issued a credential');
}
