"""Private functional storage manifests; preserve existing database state.

Milvus embedded etcd/local storage follows its official standalone deployment.
This topology is disposable functional infrastructure, not a capacity/DR claim.
"""
import copy
import re

OWNER = 'aether-continuous-quality'
MILVUS = 'milvusdb/milvus@sha256:82630c952e887e30b0b09f396cb1fbd4320dea25b4f7a3ba12bf1e10524c735c'


def namespace_guard(namespace):
    if not re.fullmatch(r'aether-quality-[a-z0-9-]{1,30}', namespace):
        raise ValueError('owned quality namespace required')


def add_database_tls(deployment):
    meta = deployment['metadata']
    namespace_guard(meta['namespace'])
    name = meta['name']
    if meta.get('labels', {}).get('owner') != OWNER or name not in ['postgres', 'redis']:
        raise ValueError('owned quality database required')
    result = copy.deepcopy(deployment)
    pod = result['spec']['template']['spec']
    if any(v['name'] == 'quality-tls' for v in pod.get('volumes', [])):
        raise ValueError('TLS already configured; inspect before changing certificates')
    container, = pod['containers']
    if container['name'] != name:
        raise ValueError('unexpected database container')
    data_path = '/var/lib/postgresql/data' if name == 'postgres' else container.get('workingDir', '/data')
    for env in container.get('env', []):
        if name == 'postgres' and env['name'] == 'PGDATA':
            data_path = env.get('value', '')
    if not data_path.startswith('/') or '..' in data_path.split('/'):
        raise ValueError('cannot prove persistent database directory')
    candidates = [v for v in container.get('volumeMounts', [])
                  if data_path == v['mountPath'].rstrip('/') or
                  data_path.startswith(v['mountPath'].rstrip('/') + '/')]
    mounted = max(candidates, key=lambda v: len(v['mountPath']), default={})
    data = next((v for v in pod.get('volumes', []) if v['name'] == mounted.get('name')), {})
    if 'persistentVolumeClaim' not in data:
        raise ValueError('database rollout requires persistent actual data directory; emptyDir would be lost')
    pod.setdefault('volumes', []).append({'name': 'quality-tls', 'secret': {
        'secretName': 'quality-' + name + '-tls', 'defaultMode': 0o640}})
    container.setdefault('volumeMounts', []).append({
        'name': 'quality-tls', 'mountPath': '/tls', 'readOnly': True})
    if name == 'postgres':
        if container.get('args') or container.get('command'):
            raise ValueError('unexpected PostgreSQL startup override')
        pod.setdefault('securityContext', {})['fsGroup'] = 999
        container['args'] = ['-c', 'ssl=on', '-c', 'ssl_cert_file=/tls/server.crt',
                             '-c', 'ssl_key_file=/tls/server.key',
                             '-c', 'ssl_ca_file=/tls/ca.crt']
    else:
        expected = ['sh', '-c', 'exec redis-server --requirepass "$REDIS_PASSWORD"']
        if container.get('command') not in [None, expected]:
            raise ValueError('unexpected Redis startup override')
        container['command'] = ['sh', '-c',
            'exec redis-server --requirepass "$REDIS_PASSWORD" --port 6379 --tls-port 6380 '
            '--tls-cert-file /tls/server.crt --tls-key-file /tls/server.key '
            '--tls-ca-cert-file /tls/ca.crt --tls-auth-clients no']
    return result


def milvus_manifests(namespace):
    namespace_guard(namespace)
    def obj(kind, name, **body):
        return {'apiVersion': 'apps/v1' if kind == 'Deployment' else 'v1',
                'kind': kind, 'metadata': {'name': name, 'namespace': namespace,
                'labels': {'owner': OWNER}}, **body}
    config = '''common:
  storageType: local
  security:
    authorizationEnabled: true
    defaultRootPassword: ${MILVUS_ROOT_PASSWORD}
    tlsMode: 1
proxy:
  http:
    enabled: false
etcd:
  use:
    embed: true
  data:
    dir: /var/lib/milvus/etcd
  config:
    path: /milvus/configs/embedEtcd.yaml
tls:
  serverPemPath: /tls/server.crt
  serverKeyPath: /tls/server.key
  caPemPath: /tls/ca.crt
log:
  level: warn
'''
    embed = '''listen-client-urls: http://127.0.0.1:2379
advertise-client-urls: http://127.0.0.1:2379
quota-backend-bytes: 268435456
auto-compaction-mode: revision
auto-compaction-retention: '1000'
'''
    # Resolve the random hexadecimal password in memory; never write a Secret
    # into a ConfigMap or a process log.
    cmd = ('cp -R /milvus/configs/. /config/; '
           'sed "s/\\${MILVUS_ROOT_PASSWORD}/$MILVUS_ROOT_PASSWORD/g" '
           '/template/user.yaml > /config/user.yaml; '
           'cp /template/embedEtcd.yaml /config/embedEtcd.yaml; '
           'exec milvus run standalone')
    container = {'name': 'milvus', 'image': MILVUS, 'command': ['sh', '-ec', cmd],
        'env': [{'name': 'MILVUS_ROOT_PASSWORD', 'valueFrom': {'secretKeyRef': {
                'name': 'quality-milvus-auth', 'key': 'root-password'}}},
                {'name': 'ETCD_USE_EMBED', 'value': 'true'},
                {'name': 'ETCD_DATA_DIR', 'value': '/var/lib/milvus/etcd'},
                {'name': 'ETCD_CONFIG_PATH', 'value': '/config/embedEtcd.yaml'},
                {'name': 'COMMON_STORAGETYPE', 'value': 'local'},
                {'name': 'DEPLOY_MODE', 'value': 'STANDALONE'}],
        'resources': {'requests': {'cpu': '250m', 'memory': '512Mi'},
                      'limits': {'cpu': '1', 'memory': '3Gi'}},
        'securityContext': {'allowPrivilegeEscalation': False},
        'ports': [{'containerPort': 19530}],
        'startupProbe': {'httpGet': {'path': '/healthz', 'port': 9091},
                         'periodSeconds': 5, 'timeoutSeconds': 3, 'failureThreshold': 90},
        'readinessProbe': {'httpGet': {'path': '/healthz', 'port': 9091},
                           'periodSeconds': 10, 'timeoutSeconds': 3},
        'volumeMounts': [{'name': 'data', 'mountPath': '/var/lib/milvus'},
                        {'name': 'template', 'mountPath': '/template', 'readOnly': True},
                        {'name': 'config', 'mountPath': '/config'},
                        {'name': 'tls', 'mountPath': '/tls', 'readOnly': True}]}
    container['env'].append({'name': 'MILVUSCONF', 'value': '/config'})
    pod = {'automountServiceAccountToken': False, 'enableServiceLinks': False,
           'containers': [container], 'volumes': [
               {'name': 'data', 'emptyDir': {'sizeLimit': '4Gi'}},
               {'name': 'template', 'configMap': {'name': 'quality-milvus-config'}},
               {'name': 'config', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '8Mi'}},
               {'name': 'tls', 'secret': {'secretName': 'quality-milvus-tls'}}]}
    return [obj('ConfigMap', 'quality-milvus-config', data={'user.yaml': config, 'embedEtcd.yaml': embed}),
            obj('Service', 'milvus', spec={'type': 'ClusterIP', 'selector': {'app': 'quality-milvus'},
                'ports': [{'port': 19530, 'targetPort': 19530}]}),
            obj('Deployment', 'milvus', spec={'replicas': 1, 'strategy': {'type': 'Recreate'},
                'selector': {'matchLabels': {'app': 'quality-milvus'}},
                'template': {'metadata': {'labels': {'app': 'quality-milvus', 'owner': OWNER}}, 'spec': pod}})]
