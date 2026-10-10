"""Fixed identity contract for the private quality P3 instance.

This configuration does not claim full storage/model readiness or provision
business records. Native RuoYi authentication remains the source of authority.
"""
import re


def identity_configuration(namespace):
    if not re.fullmatch(r'aether-quality-[a-z0-9-]{1,30}',namespace):
        raise ValueError('owned quality namespace required')
    host=f'backend.{namespace}.svc.cluster.local'
    permissions=['memory:read','memory:write','memory:correct','memory:delete','memory:history']
    mappings=[]
    for user,tenant in [('50004','501'),('50005','501'),('50006','502')]:
        mappings.append({'ruoyi_user_id':user,'ruoyi_tenant_id':tenant,
            'business_user_id':'ry_user_'+user,'business_tenant_id':'ry_tenant_'+tenant,
            'principal_id':'ry_principal_'+user,'application_id':'p3','agent_id':'p3-agent'})
    return {'revision':1,'tenants':[{'tenant_id':'ry_tenant_501'},{'tenant_id':'ry_tenant_502'}],
        'identities':[], 'ruoyi':{'base_url':f'http://{host}:48080','trusted_http_host':host,
            'issuer':'aether-quality-ruoyi','client_id':'aether-quality',
            'client_secret_file':'/identity-client/client-secret','allowed_client_ids':['default'],
            'auto_provision':False,'mappings':mappings,
            'role_permissions':{'aether_user':permissions,'aether_tenant_admin':permissions}}}


def service_client_request(secret):
    if not secret:
        raise ValueError('service client secret required')
    return {'clientId':'aether-quality','secret':secret,'name':'Aether isolated quality P3',
        'logo':'http://127.0.0.1:14883/ruoyi/favicon.ico',
        'description':'Private test identity inspection only; no user login grants',
        'status':0,'accessTokenValiditySeconds':300,'refreshTokenValiditySeconds':600,
        'redirectUris':[],'authorizedGrantTypes':[],'scopes':[],'autoApproveScopes':[],
        'authorities':[],'resourceIds':[]}
