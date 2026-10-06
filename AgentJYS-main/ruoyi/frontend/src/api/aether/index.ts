import request from '@/config/axios'
export const getIdentity = () => request.get({ url: '/aether/identity/self' })
export const getOperations = (resource: string, params: Record<string, any> = {}) =>
  request.get({ url: `/aether/ops/${resource}`, params })
export const sendCommand = (data: Record<string, any>) =>
  request.post({ url: '/aether/ops/commands', data })
export const getConsole = (resource: string, params: Record<string, any> = {}) =>
  request.get({
    url: `/aether/ops/console/${resource}`,
    params,
    headers: { 'Cache-Control': 'no-store', Pragma: 'no-cache' }
  })
