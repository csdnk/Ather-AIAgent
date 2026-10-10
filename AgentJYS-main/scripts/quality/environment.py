"""Provision private, bounded CI databases in an explicitly owned namespace.

No shared workload is modified. Passwords are generated into Kubernetes Secrets,
never output or written to the source tree. Storage is disposable emptyDir: this
environment is for functional integration, not a claim of node-loss durability.
"""

import argparse
import json
import re
import secrets
import subprocess
from pathlib import Path

OWNER = 'aether-continuous-quality'


def verify_owner(value):
    if value.get('metadata', {}).get('labels', {}).get('owner') != OWNER:
        raise ValueError('resource ownership does not match continuous quality')


def manifests(namespace):
    if not re.fullmatch(r'aether-quality-[a-z0-9-]{1,30}', namespace):
        raise ValueError('an explicit aether-quality- namespace is required')

    def resource(kind, name, spec):
        return {'apiVersion': 'apps/v1' if kind == 'Deployment' else 'v1',
                'kind': kind, 'metadata': {'name': name, 'namespace': namespace,
                                         'labels': {'owner': OWNER}}, 'spec': spec}

    result = [
        {'apiVersion': 'v1', 'kind': 'Namespace',
         'metadata': {'name': namespace, 'labels': {'owner': OWNER,
                      'purpose': 'continuous-quality'}}},
        resource('ResourceQuota', 'quality-budget', {'hard': {
            'requests.cpu': '4', 'requests.memory': '8Gi', 'limits.cpu': '10',
            'limits.memory': '20Gi', 'requests.storage': '0', 'pods': '16',
            'services.loadbalancers': '0', 'services.nodeports': '0'}}),
        resource('LimitRange', 'quality-limits', {'limits': [{
            'type': 'Container', 'defaultRequest': {'cpu': '100m', 'memory': '128Mi'},
            'default': {'cpu': '1', 'memory': '1Gi'}}]}),
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
         'metadata': {'name': 'private-ingress', 'namespace': namespace,
                      'labels': {'owner': OWNER}},
         'spec': {'podSelector': {}, 'policyTypes': ['Ingress'], 'ingress': [{
             'from': [{'namespaceSelector': {'matchLabels': {
                 'kubernetes.io/metadata.name': namespace}}}]}]}}
    ]
    configs = [
        ('postgres', 'postgres:17.6', 5432, '/var/lib/postgresql/data',
         [{'name': 'POSTGRES_DB', 'value': 'aether_quality'},
          {'name': 'POSTGRES_PASSWORD', 'valueFrom': {'secretKeyRef': {
              'name': 'quality-database', 'key': 'postgres-password'}}}],
         ['pg_isready', '-U', 'postgres', '-d', 'aether_quality']),
        ('mysql', 'mysql:8.0', 3306, '/var/lib/mysql',
         [{'name': 'MYSQL_DATABASE', 'value': 'ruoyi_quality'},
          {'name': 'MYSQL_ROOT_PASSWORD', 'valueFrom': {'secretKeyRef': {
              'name': 'quality-database', 'key': 'mysql-password'}}}],
         ['sh', '-c', 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h127.0.0.1 -uroot -e "SELECT 1"']),
        ('redis', 'redis:7-alpine', 6379, '/data',
         [{'name': name, 'valueFrom': {'secretKeyRef': {
             'name': 'quality-database', 'key': 'redis-password'}}}
          for name in ['REDIS_PASSWORD', 'REDISCLI_AUTH']], ['redis-cli', 'ping']),
    ]
    for name, image, port, mount, env, probe in configs:
        container = {'name': name, 'image': image, 'env': env,
                     'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'},
                                   'limits': {'cpu': '1', 'memory': '1Gi'}},
                     'ports': [{'containerPort': port}],
                     'securityContext': {'allowPrivilegeEscalation': False},
                     'readinessProbe': {'exec': {'command': probe}, 'periodSeconds': 5,
                                        'timeoutSeconds': 3, 'failureThreshold': 12},
                     'volumeMounts': [{'name': 'data', 'mountPath': mount}]}
        if name == 'redis':
            container['command'] = ['sh', '-c', 'exec redis-server --requirepass "$REDIS_PASSWORD"']
        result += [resource('Deployment', name, {
            'replicas': 1, 'strategy': {'type': 'Recreate'},
            'selector': {'matchLabels': {'app': name}},
            'template': {'metadata': {'labels': {'app': name, 'owner': OWNER}},
                         'spec': {'automountServiceAccountToken': False,
                                  'containers': [container],
                                  'volumes': [{'name': 'data', 'emptyDir': {
                                      'sizeLimit': '4Gi'}}]}}}),
                   resource('Service', name, {'type': 'ClusterIP',
                       'selector': {'app': name}, 'ports': [{'port': port, 'targetPort': port}]})]
    return result


def provision(kubeconfig, namespace, output):
    objects = manifests(namespace)
    output = Path(output).resolve()
    repository = Path(__file__).resolve().parents[3]
    if output == repository or repository in output.parents:
        raise ValueError('evidence must be outside the repository')
    output.mkdir(parents=True, exist_ok=False)
    command = ['kubectl', '--kubeconfig', str(kubeconfig), '--request-timeout=30s']

    def call(args, data=None):
        p = subprocess.run(command+args, input=data, capture_output=True, timeout=60)
        if p.returncode:
            # API failures may contain Secret request bodies. Keep those private.
            raise RuntimeError('Kubernetes operation failed: '+args[0])
        return p.stdout

    def ensure(obj):
        args = ['-n', namespace] if obj['kind'] != 'Namespace' else []
        existing = call([*args, 'get', obj['kind'], obj['metadata']['name'],
                         '--ignore-not-found', '-o', 'json'])
        if existing.strip():
            verify_owner(json.loads(existing))
        call([*args, 'apply', '-f', '-'], json.dumps(obj).encode())

    ensure(objects[0])
    existing = call(['-n', namespace, 'get', 'secret', 'quality-database',
                     '--ignore-not-found', '-o', 'json'])
    if existing.strip():
        secret = json.loads(existing)
        verify_owner(secret)
        if 'redis-password' not in secret.get('data', {}):
            ensure({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {
                'name': 'quality-database', 'namespace': namespace, 'labels': {'owner': OWNER}},
                'data': secret['data'], 'stringData': {'redis-password': secrets.token_urlsafe(32)}})
    else:
        ensure({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {
            'name': 'quality-database', 'namespace': namespace, 'labels': {'owner': OWNER}},
            'stringData': {'postgres-password': secrets.token_urlsafe(32),
                           'mysql-password': secrets.token_urlsafe(32),
                           'redis-password': secrets.token_urlsafe(32)}})
    for obj in objects[1:]:
        ensure(obj)
    report = {'namespace': namespace, 'owner': OWNER, 'objects': [
        {'kind': x['kind'], 'name': x['metadata']['name']} for x in objects],
        'state': 'PROVISIONED_NOT_READY_VERIFIED', 'storage': 'disposable-emptyDir',
        'new_nodes': 0, 'new_managed_disks': 0, 'public_services': 0}
    (output/'environment.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    provision(args.kubeconfig, args.namespace, args.output)
