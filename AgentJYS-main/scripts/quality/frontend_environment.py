"""Private RuoYi browser entry, using the existing pinned frontend image."""
import re

OWNER='aether-continuous-quality'
IMAGE='aetherp3acr-a0bne7gpetbpdcbq.azurecr.io/aether/ruoyi-frontend@sha256:1c49486855e9b8b9e85619c59ec13e767382fac2a69374b0991a58b30d43fb74'


def manifests(namespace):
    if not re.fullmatch(r'aether-quality-[a-z0-9-]{1,30}',namespace):
        raise ValueError('owned quality namespace required')
    def obj(kind,name,**body):
        return {'apiVersion':'apps/v1' if kind=='Deployment' else 'v1','kind':kind,
            'metadata':{'name':name,'namespace':namespace,'labels':{'owner':OWNER}},**body}
    nginx='''server {
    listen 8080;
    server_name _;
    server_tokens off;
    root /usr/share/nginx/html;
    location = /healthz { access_log off; return 200 'ok'; }
    location = / { return 302 /ruoyi/; }
    location = /ruoyi { return 308 /ruoyi/; }
    location /ruoyi/ {
        try_files $uri $uri/ /ruoyi/index.html;
        add_header X-Content-Type-Options nosniff always;
    }
    location /ruoyi-api/admin-api/infra/file/ {
        proxy_pass http://backend:48080/admin-api/infra/file/;
    }
    location /ruoyi-api/ {
        proxy_pass http://backend:48080/admin-api/;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 60s;
    }
    location / { return 404; }
}
'''
    pod={'automountServiceAccountToken':False,'enableServiceLinks':False,
        'imagePullSecrets':[{'name':'acr-pull'}],
        'containers':[{'name':'frontend','image':IMAGE,
            'ports':[{'containerPort':8080}],
            'resources':{'requests':{'cpu':'50m','memory':'64Mi'},'limits':{'cpu':'500m','memory':'256Mi'}},
            'securityContext':{'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
            'readinessProbe':{'httpGet':{'path':'/healthz','port':8080},'periodSeconds':5},
            'volumeMounts':[{'name':'config','mountPath':'/etc/nginx/conf.d/default.conf',
                            'subPath':'default.conf','readOnly':True}]}],
        'volumes':[{'name':'config','configMap':{'name':'quality-frontend-config'}}]}
    return [obj('ConfigMap','quality-frontend-config',data={'default.conf':nginx}),
        obj('Service','frontend',spec={'type':'ClusterIP','selector':{'app':'quality-frontend'},
            'ports':[{'port':8080,'targetPort':8080}]}),
        obj('Deployment','frontend',spec={'replicas':1,'selector':{'matchLabels':{'app':'quality-frontend'}},
            'template':{'metadata':{'labels':{'app':'quality-frontend','owner':OWNER}},'spec':pod}})]
