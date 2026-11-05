"""The same source-to-app mapping for deployment selection and Radix findings."""
MAIN_PREFIXES = (
    'tank/', 'workspace/', 'litellm/', 'hermes/', 'db/', 'searxng/', 'tiger/', 'site/', 'tor/',
    'docker-compose.yml', 'docker-compose.za.yml', 'jobs/train/backup.py',
    'jobs/train/r2_archive.py', 'jobs/train/Dockerfile.backup',
)
ALL_ROOMS_PREFIXES = ('agents/_chassis/', 'agents/_template/')
ROOMS = ('base','ghost','market-lab','radix','rig','tiger','tisan','trigger','zenas')


def finding_apps(path):
    if not isinstance(path,str) or path.startswith(('/', '\\')) or '..' in path.split('/'):
        return []
    if path.startswith(MAIN_PREFIXES):
        return ['main','tervory-tank-za']
    parts=path.split('/')
    rooms = ROOMS if path.startswith(ALL_ROOMS_PREFIXES) else [parts[1].replace('_','-')] if len(parts)>2 and parts[0]=='agents' and not parts[1].startswith('_') else []
    return [app for room in rooms if room in ROOMS for app in ('agent-'+room,'agent-'+room+'-za')]


def valid_app(app):
    return app in {'main','tervory-tank-za',*(name for room in ROOMS for name in ('agent-'+room,'agent-'+room+'-za'))}
