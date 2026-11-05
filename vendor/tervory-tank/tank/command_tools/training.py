"""Training command declarations; argument classes remain exported by command."""
TOOLS = {}
FOUNDER_TOOLS = ["training.status", "training.run", "training.receipt"]
TENANT_ADMIN_TOOLS = []
ADMIN_ONLY_TOOLS = REQUEST_BODY_TOOLS = frozenset()
CONFIRMED = frozenset({"training.run"})

def adapters(command):
    return {
        'training.status': command.Adapter('GET', '/v1/training/status', command.EmptyArgs, 'training.status'),
        'training.run': command.Adapter('POST', '/v1/training/run', command.TrainingRunArgs, 'Start Zenas training', True, 'paid_run'),
        'training.receipt': command.Adapter('POST', '/v1/training/receipt', command.TrainingReceiptArgs, 'Record training receipt'),
    }
