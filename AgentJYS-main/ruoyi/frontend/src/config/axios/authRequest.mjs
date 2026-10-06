// Authentication exemptions must match endpoints, never a fragment such as /login.
export const isPublicAuthRequest = (url = '') =>
  ['/aether/identity/login', '/system/auth/login', '/system/auth/refresh-token'].includes(
    String(url).split('?')[0].replace(/\/+$/, '')
  )

export const isLoginPage = (pathname = '') =>
  String(pathname).replace(/\/+$/, '').split('/').pop() === 'login'
