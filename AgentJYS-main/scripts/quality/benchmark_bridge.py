"""Loopback-only Apifox job adapter. HTTP callers can select two fixed profiles only.

The trusted local config chooses the executor and assets. It is never accepted
over HTTP. Long polling queries the original job; it does not rerun a benchmark.
"""
import argparse
import hashlib
import hmac
import json
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ORIGIN = 'https://app.apifox.com'
TERMINAL = {'PASS', 'FAIL', 'BLOCKED'}


class Conflict(ValueError):
    pass


def authorized(host, origin, header, token):
    return (host == '127.0.0.1:14884' and origin in (None, ORIGIN)
            and bool(token) and hmac.compare_digest(header or '', 'Bearer ' + token))


def validate(body, profiles=('embedding', 'compression')):
    if not isinstance(body, dict) or set(body) != {'profile', 'request_id'}:
        raise ValueError('Only profile and request_id are accepted')
    if body['profile'] not in profiles:
        raise ValueError('Unknown fixed profile')
    if not isinstance(body['request_id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', body['request_id']):
        raise ValueError('Invalid request identity')
    return body


class Manager:
    def __init__(self, root, executor, max_jobs=3, profiles=('embedding', 'compression')):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.executor, self.max_jobs = executor, max_jobs
        self.profiles = tuple(profiles)
        self.lock = threading.Condition()
        self.jobs = {}
        self.session_jobs = 0
        for file in self.root.glob('*/job.json'):
            job = json.loads(file.read_text(encoding='utf-8'))
            if job['status'] == 'RUNNING':
                job.update(status='FAIL', report={'status': 'FAIL', 'reason': 'RUNNER_RESTARTED', 'release_gate': 'BLOCKED', 'cleanup_pending': True})
            self.jobs[job['job_id']] = job
            self._save(job)

    def _save(self, job):
        path = self.root / job['job_id']
        path.mkdir(exist_ok=True)
        temp = path / 'job.tmp'
        temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path / 'job.json')

    def submit(self, body):
        validate(body, self.profiles)
        with self.lock:
            for job in self.jobs.values():
                if job['request_id'] == body['request_id']:
                    if job['profile'] != body['profile']:
                        raise Conflict('Request identity already belongs to another profile')
                    return dict(job)
            if self.session_jobs >= self.max_jobs or any(j['status'] == 'RUNNING' or j.get('report', {}).get('cleanup_pending') for j in self.jobs.values()):
                raise Conflict('Single worker or session job budget reached')
            job = dict(body, job_id=str(uuid.uuid4()), status='RUNNING', created_at=time.time())
            self.jobs[job['job_id']] = job
            self.session_jobs += 1
            self._save(job)
            threading.Thread(target=self._run, args=(job['job_id'],), daemon=False).start()
            return dict(job)

    def _run(self, job_id):
        job = self.jobs[job_id]
        try:
            report = self.executor(job['profile'], self.root / job_id)
            if report.get('status') not in TERMINAL:
                raise ValueError('Executor did not return a terminal report')
            valid = report.get('metrics', {}).get('completed_valid') if job['profile'] in ('embedding', 'compression') else (report.get('records') and all(r.get('status') in ('PASS', 'N/A') for r in report['records']) and not report.get('cleanup_pending', True))
            if report['status'] == 'PASS' and (not report.get('run_id') or not valid):
                raise ValueError('No successful computation evidence')
            report['release_gate'] = 'BLOCKED'
        except Exception:
            report = {'status': 'FAIL', 'reason': 'EXECUTOR_FAILED_SEE_LOCAL_EVIDENCE', 'release_gate': 'BLOCKED', 'cleanup_pending': True}
        with self.lock:
            job.update(status=report['status'], report=report, finished_at=time.time())
            self._save(job)
            self.lock.notify_all()

    def wait(self, job_id, seconds=0):
        with self.lock:
            job = self.jobs[job_id]
            self.lock.wait_for(lambda: job['status'] != 'RUNNING', timeout=seconds)
            return json.loads(json.dumps(job))


def executor(config):
    """Run the locally configured owned-namespace adapter, never request arguments."""
    def run(profile, jobdir):
        command = [config['python'], config['adapter'], '--profile', profile, '--job-dir', str(jobdir),
                   '--config', config['adapter_config']]
        with (jobdir/'executor.log').open('wb') as log:
            try:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
            except subprocess.TimeoutExpired:
                # The supervisor retains cleanup ownership after killing the worker.
                cleanup = subprocess.run(command + ['--cleanup-only'], stdout=log, stderr=subprocess.STDOUT, timeout=180)
                # A killed adapter may have an in-flight child create request.
                # Even an absent Pod now cannot prove that it will not appear later.
                return {'status': 'FAIL', 'reason': 'EXECUTOR_DEADLINE', 'cleanup_pending': True,
                        'cleanup_attempt_succeeded': cleanup.returncode == 0, 'release_gate': 'BLOCKED'}
        report = json.loads((jobdir/'summary.json').read_text(encoding='utf-8'))
        if result.returncode != {'PASS': 0, 'FAIL': 1, 'BLOCKED': 2}.get(report.get('status')):
            raise ValueError('Exit status disagrees with report')
        return report
    return run


def handler(manager, token):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def log_message(self, *_):
            pass  # No tokens or payloads in HTTP access logs.

        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def reply(self, code, data):
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            if self.headers.get('Origin') == ORIGIN:
                self.send_header('Access-Control-Allow-Origin', ORIGIN)
                self.send_header('Vary', 'Origin')
            self.end_headers()
            self.wfile.write(payload)

        def do_OPTIONS(self):
            print(json.dumps({'method': 'OPTIONS', 'origin': self.headers.get('Origin'),
                'requested_headers': self.headers.get('Access-Control-Request-Headers')}), flush=True)
            if self.headers.get('Host') != '127.0.0.1:14884' or self.headers.get('Origin') != ORIGIN:
                return self.reply(403, {'error': 'Origin denied'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', ORIGIN)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type, Cache-Control')
            self.end_headers()

        def authenticated(self):
            print(json.dumps({'method': self.command, 'origin': self.headers.get('Origin')}), flush=True)
            if not authorized(self.headers.get('Host'), self.headers.get('Origin'), self.headers.get('Authorization'), token):
                self.reply(401, {'error': 'Local runner authorization required'})
                return False
            return True

        def do_GET(self):
            if not self.authenticated(): return
            if self.path == '/v1/capabilities':
                return self.reply(200, {'profiles': list(manager.profiles), 'max_parallel': 1,
                    'max_jobs_per_start': manager.max_jobs, 'new_paid_resources': False, 'release_gate': 'BLOCKED'})
            match = re.fullmatch(r'/v1/jobs/([a-f0-9-]{36})(/report|\?wait=20)?', self.path)
            if not match: return self.reply(404, {'error': 'Unknown route'})
            try:
                job = manager.wait(match[1], 20 if match[2] == '?wait=20' else 0)
                if match[2] == '/report' and job['status'] == 'RUNNING':
                    return self.reply(409, {'job_id': job['job_id'], 'status': 'RUNNING'})
                return self.reply(200, job)
            except KeyError:
                return self.reply(404, {'error': 'Unknown job'})

        def do_POST(self):
            if not self.authenticated(): return
            if self.path != '/v1/jobs': return self.reply(404, {'error': 'Unknown route'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 512 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Body size invalid')
                body = json.loads(self.rfile.read(size))
                job = manager.submit(body)
                self.reply(202 if job['status'] == 'RUNNING' else 200, job)
            except Conflict as e:
                self.reply(409, {'error': str(e)})
            except (ValueError, TypeError):
                self.reply(400, {'error': 'Invalid fixed-profile request'})
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    token = Path(config['token_file']).read_text(encoding='utf-8').strip()
    if len(token) < 32: raise ValueError('Local token must have at least 32 characters')
    # Acquire the singleton socket before touching persisted job evidence.
    server = ThreadingHTTPServer(('127.0.0.1', 14884), BaseHTTPRequestHandler)
    manager = Manager(config['results'], executor(config), max_jobs=3)
    server.RequestHandlerClass = handler(manager, token)
    # A deliberately bounded local session, never an unattended pressure service.
    threading.Timer(7200, server.shutdown).start()
    print('Aether local benchmark adapter ready on 127.0.0.1:14884 (2h; 3 jobs)', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
